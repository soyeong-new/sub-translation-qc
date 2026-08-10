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
    'category (문자열, 반드시 다음 중 하나: '
    '"sensitivity"(사전에 없어 애매한 비속어), '
    '"mistranslation"(의미가 잘못 옮겨졌거나 함축된 의미가 빠진 경우), '
    '"nuance_tone"(뉘앙스·어조가 원문과 다른 경우), '
    '"unnatural_style"(문법은 맞지만 한국어 구조를 그대로 따라간 직역투·어색한 흐름), '
    '"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현)), '
    "corrected_text (문자열, 최종 교정된 전체 대상언어 텍스트), "
    "description (문자열, 한국어로 무엇을 왜 고쳤는지). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)

_BACK_TRANSLATE_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "korean_text (문자열, 자연스러운 한국어 역번역)."
)

_EQUIVALENCE_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "equivalent (불리언, text_a와 text_b가 같은 문제를 같은 방식으로 고친 "
    "것이면 true, 단어 선택이 달라도 무방하다 — 실질적으로 다른 내용·뉘앙스·"
    "해결책이면 false)."
)

_SCENE_SPLIT_SYSTEM_PREFIX = (
    "다음은 한 영화/영상의 대사 목록이다(korean_text=한국어 원문, "
    "target_text=대상언어 자막, start/end=초 단위 타임코드). 처음부터 끝까지 "
    "순서대로 훑으며, 다음 4가지 기준 중 하나라도 뚜렷하게 나타나는 지점에서 "
    "씬(scene) 경계를 그어라: "
    "1) 화제/주제가 완전히 바뀔 때, "
    "2) 대화에 참여하는 인물 구성이 바뀔 때(새 인물 등장/퇴장), "
    "3) 장면·시간적 흐름이 바뀔 때(특히 타임코드 공백이 3~5초 이상인 지점), "
    "4) 가볍던 분위기가 진지/대립으로(또는 그 반대로) 반전될 때. "
    "모든 대사는 반드시 어느 한 씬에 속해야 하며(빠짐없이), 두 씬에 걸치거나 "
    "겹쳐선 안 되고, 입력 순서를 그대로 유지해야 한다(첫 씬의 start_id는 "
    "입력의 첫 id, 한 씬의 end_id 바로 다음 id가 다음 씬의 start_id)."
)

_SCENE_SPLIT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "scene_split",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_id": {"type": "string"},
                            "end_id": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["start_id", "end_id", "summary"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["scenes"],
            "additionalProperties": False,
        },
    },
}


def _language_label(profile: dict) -> str:
    language = profile.get("language") or "대상언어"
    variant = profile.get("variant")
    return f"{language}({variant})" if variant else language


class GptClient:
    def __init__(self, api_key: str, model: str, transcribe_model: str = "whisper-1"):
        self._model = model
        self._transcribe_model = transcribe_model
        self._sdk_client = AsyncOpenAI(api_key=api_key)

    async def _call(self, system: str, user: str, key: str = "findings", label: str = "") -> List[dict]:
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
            items = parsed[key]
            if not isinstance(items, list):
                raise TypeError(f"{key}가 리스트가 아님")
            return items
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            prefix = f"GPT {label} 응답" if label else "GPT 응답"
            raise ValueError(f"{prefix}이 기대한 JSON 형태가 아님: {preview}") from exc

    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        """correct_primary(Claude)와 대칭적으로 원본을 처음부터 독립적으로
        검토한다. Claude가 이 텍스트를 이미 봤는지, 뭘 고쳤는지는 전혀 모른다
        — 이전 교정 여부를 알려주면 앵커링 편향으로 독립적 재판단보다 그냥
        승인하는 쪽으로 기울어 정확도가 떨어진다(파이프라인이 두 모델의
        일치/불일치를 이후 단계에서 병합해 신뢰도 신호로 쓴다)."""
        language_label = _language_label(profile)
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")

        system = (
            f"너는 한국어-{language_label} 자막의 검증자다. korean_text(원문)와 "
            "target_text를 나란히 놓고 다음 기준으로 처음부터 독립적으로 검증·"
            "교정하라: 사전에 없는 애매한 비속어, 번역정확성, 문화맥락, 뉘앙스어조, "
            "자연스러운흐름(직역 지양, 한국어 어순을 그대로 따라간 부분을 찾아 "
            "고칠 것), 함축의미, 로컬라이제이션(그 나라 문화에 맞는 표현인지). "
            "성별/격식(존댓말·반말)은 화자를 특정할 근거가 없어 다루지 마라 — "
            "검수자가 영상을 보고 직접 확인한다. 단, 입력 pair에 "
            "resolved_gender(male/female) 또는 resolved_formality(formal/"
            "informal)가 이미 주어져 있으면 예외다 — 그건 이미 확정된 값이니, "
            "네가 제안하는 corrected_text가 그 성별/격식과 문법적으로 반드시 "
            "일치하게 하라(예: resolved_gender가 female이면 성별 표시 형용사/"
            "분사는 여성형). resolved_gender/resolved_formality가 없는 pair는 "
            "그대로 다루지 마라 — 추측 금지. 새 인물 이름의 표기 통일도 "
            "다루지 마라(별도 사전으로 관리). "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
        )
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        if naturalness_instruction:
            system += f"자연스러움 지침: {naturalness_instruction}\n"
        system += _JSON_INSTRUCTION + "\n" + _VERIFY_SCHEMA_INSTRUCTION
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        return await self._call(system, user)

    async def back_translate(self, texts: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 텍스트 목록이다. 각 항목을 자연스러운 "
            "한국어로 역번역하라 — 스페인어를 모르는 검수자가 원래 의미를 "
            "가늠하기 위한 참고용이므로, 의역보다 원문 의미를 최대한 그대로 "
            "전달하는 것을 우선하라.\n" + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call(system, user, key="results", label="역번역")

    async def check_equivalence(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과, 그걸 {language_label}로 교정한 "
            "두 후보 문구(text_a, text_b) 목록이다. 각 항목마다 text_a와 "
            "text_b가 같은 문제를 같은 방식으로 고친 것인지 판단하라.\n"
            + _EQUIVALENCE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="동등성 확인")

    async def split_scenes(self, pairs: List[dict], profile: dict) -> List[dict]:
        """씬 분할 전용 콜. json_schema strict 모드로 출력 형식을 API 레벨에서
        강제한다 — correct_primary/verify_and_refine이 쓰는 json_object 모드보다
        한 단계 더 강한 보장이다(스키마를 벗어난 키/누락이 애초에 생성되지
        않음). Claude와 교차검증하지 않는 이유는 씬 경계 판단이 두 모델의
        일치 여부로 신뢰도를 매길 "정답 있는 판정"이 아니라 기계적 전처리라서다
        (호출자가 경계 유효성 자체는 별도로 검증한다)."""
        language_label = _language_label(profile)
        system = f"{_SCENE_SPLIT_SYSTEM_PREFIX} 대상언어는 {language_label}이다."
        user = json.dumps(pairs, ensure_ascii=False)
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format=_SCENE_SPLIT_SCHEMA,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if not response.choices:
            raise ValueError("GPT 씬 분할 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            scenes = json.loads(text)["scenes"]
            if not isinstance(scenes, list):
                raise TypeError("scenes가 리스트가 아님")
            return scenes
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 씬 분할 응답이 기대한 JSON 형태가 아님: {preview}") from exc

    async def transcribe(self, audio_path: str) -> List[dict]:
        """audio_path 하나의 STT 결과를 세그먼트 리스트로 반환한다. 호출자
        (pipeline._transcribe_in_chunks)가 긴 오디오를 여러 조각으로 나눠 이
        메서드를 조각당 한 번씩 호출하므로, 세그먼트가 하나도 없는 것(무음
        구간, 엔드크레딧 등)은 그 조각만 보면 지극히 정상이다 — 여기서
        실패로 처리하지 않고 빈 리스트를 반환한다. "에피소드 전체에 대사가
        없음"이라는 진짜 실패 판단은 모든 조각을 병합한 뒤 호출자 쪽에서
        한다."""
        with open(audio_path, "rb") as f:
            response = await self._sdk_client.audio.transcriptions.create(
                model=self._transcribe_model, file=f, language="ko",
                response_format="verbose_json", timestamp_granularities=["segment"],
            )
        if not response.segments:
            return []
        return [{"start": seg.start, "end": seg.end, "text": seg.text}
                for seg in response.segments]

