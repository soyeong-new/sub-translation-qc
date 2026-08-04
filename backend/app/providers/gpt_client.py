"""GPT API로 2차 검증(원문 대조 verify+rewrite)을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from openai import AsyncOpenAI

_JSON_INSTRUCTION = (
    '반드시 {"findings": [...]} 형태의 JSON 객체만 출력하라. 수정이 필요 없는 '
    "세그먼트는 findings에 포함하지 마라."
)

_VERIFY_SCHEMA_INSTRUCTION = (
    "findings 배열의 각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열), '
    'category (문자열, 반드시 "translation" 또는 "localization" 중 하나), '
    "corrected_text (문자열, 최종 교정된 전체 대상언어 텍스트), "
    "description (문자열, 한국어로 무엇을 왜 고쳤는지). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)


class GptClient:
    def __init__(self, api_key: str, model: str, transcribe_model: str = "whisper-1"):
        self._model = model
        self._transcribe_model = transcribe_model
        self._sdk_client = AsyncOpenAI(api_key=api_key)

    async def _call(self, system: str, user: str) -> List[dict]:
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if not response.choices:
            raise ValueError("GPT 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            parsed = json.loads(text)
            findings = parsed["findings"]
            if not isinstance(findings, list):
                raise TypeError("findings가 리스트가 아님")
            return findings
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 응답이 기대한 JSON 형태가 아님: {preview}") from exc

    async def verify_and_refine(self, pairs: List[dict], original_target_by_id: dict,
                                 profile: dict, knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        """pairs의 current_text는 1차(사전필터+Claude) 결과가 이미 반영된 상태다.
        original_target_by_id는 그 이전(가장 처음의 QC언어 SRT) 텍스트로,
        "1차 교정자가 뭔가 잘못 고쳤는지" 대조하는 안전장치용 참고 자료다."""
        language = profile.get("language") or "대상언어"
        variant = profile.get("variant")
        language_label = f"{language}({variant})" if variant else language
        register_instruction = (profile.get("register_system") or {}).get("llm_instruction", "")
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")

        enriched = [
            {**p, "original_qc_srt_text": original_target_by_id.get(p["id"], p["current_text"])}
            for p in pairs
        ]
        system = (
            f"너는 한국어-{language_label} 자막의 2차(최종) 검수자다. current_text는 "
            "1차 교정자가 이미 처리한 결과다. korean_text(원문)와 나란히 놓고 다음 "
            "6개 기준으로 검증·교정하라: 번역정확성, 문화맥락, 뉘앙스어조, 화법(존댓말/"
            "반말 일관성), 자연스러운흐름(직역 지양), 함축의미. 특히 자연스러운 흐름은 "
            "korean_text와 current_text의 어순·구조를 직접 비교해, 대상 언어의 자연스러운 "
            "어순이 아니라 한국어 구조를 그대로 따라간 부분(직역투)을 찾아 고쳐라. "
            "로컬라이제이션(그 나라 문화에 맞는 표현인지)도 여기서 처음 판단한다. "
            "original_qc_srt_text는 1차 교정 전 원본이다 — 1차 교정자가 의미를 "
            "잘못 바꿨다면 이를 참고해 바로잡아라. 1차 교정이 이미 적절하면 해당 "
            "세그먼트는 결과에서 제외하라. "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
        )
        if register_instruction:
            system += f"격식 지침: {register_instruction}\n"
        if naturalness_instruction:
            system += f"자연스러움 지침: {naturalness_instruction}\n"
        system += _JSON_INSTRUCTION + "\n" + _VERIFY_SCHEMA_INSTRUCTION
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(enriched, ensure_ascii=False)
        return await self._call(system, user)

    async def transcribe(self, audio_path: str) -> List[dict]:
        with open(audio_path, "rb") as f:
            response = await self._sdk_client.audio.transcriptions.create(
                model=self._transcribe_model, file=f, language="ko",
                response_format="verbose_json", timestamp_granularities=["segment"],
            )
        if not response.segments:
            raise ValueError("GPT STT 응답에 세그먼트가 없음")
        return [{"start": seg.start, "end": seg.end, "text": seg.text}
                for seg in response.segments]

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        system = (
            "다음은 정렬된 자막 세그먼트 목록이다. 등장인물을 식별하고, "
            f"활성화된 체크 항목({profile.get('checks_enabled', {})})에 해당하는 "
            "세그먼트 id를 태깅하라. "
            '반드시 {"characters": [...], "relationships": [...]} 형태의 JSON 객체만 '
            "출력하라. characters 각 항목: "
            '{"label": 문자열, "gendered_segment_ids": [문자열]}. relationships 각 항목: '
            '{"speaker_label": 문자열, "addressee_label": 문자열, '
            '"formality_segment_ids": [문자열]}.'
        )
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(pairs, ensure_ascii=False)},
            ],
        )
        if not response.choices:
            raise ValueError("GPT 인물식별 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            parsed = json.loads(text)
            if "characters" not in parsed or "relationships" not in parsed:
                raise TypeError("characters/relationships 키가 없음")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 인물식별 응답이 기대한 형태가 아님: {preview}") from exc
