"""Claude API로 1차 교정(rewrite)과 안전망 축약을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from anthropic import AsyncAnthropic

_JSON_INSTRUCTION = (
    "반드시 JSON 배열만 출력하라. 다른 설명 텍스트를 붙이지 마라. "
    "수정이 필요 없는 세그먼트는 배열에 포함하지 마라."
)

_PRIMARY_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력 pair의 "id"와 반드시 일치), '
    'category (문자열, 반드시 "sensitivity", "glossary", "gender", "register" 중 하나), '
    "corrected_text (문자열, 교정된 전체 대상언어 텍스트), "
    "description (문자열, 한국어로 무엇을 왜 고쳤는지). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)

_SHRINK_SCHEMA_INSTRUCTION = (
    "정확히 다음 키를 가진 JSON 객체 하나만 출력하라: "
    "shrunk_text (문자열, 의미를 보존하며 글자수 제약 안으로 줄인 텍스트). "
    "다른 설명을 붙이지 마라."
)

_GRAMMAR_NECESSITY_SCHEMA_INSTRUCTION = (
    "입력 배열의 각 항목마다 정확히 하나의 결과 객체를 반환하라(빠뜨리지 마라). "
    "각 항목은 정확히 다음 키를 가져야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "gender_check_needed (불리언, 이 줄에 사람을 가리키는 성별 표시 형용사·"
    "과거분사가 있으면 true), "
    "formality_check_needed (불리언, 이 줄이 존댓말/반말(tú/usted) 선택이 "
    "걸리는 대화체 문장이면 true). "
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라.'
)


class ClaudeClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._sdk_client = AsyncAnthropic(api_key=api_key)

    def _extract_text(self, response) -> str:
        # content가 빈 리스트면 (모델이 콘텐츠 블록을 생성하지 않은 경우)
        # response.content[0]에서 바로 IndexError가 나므로, 의도한 ValueError로
        # 먼저 막아준다.
        if not response.content:
            raise ValueError("Claude 응답이 비어 있음")
        return response.content[0].text

    async def _call_array(self, system: str, user: str) -> List[dict]:
        response = await self._sdk_client.messages.create(
            model=self._model, max_tokens=4096, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = self._extract_text(response)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                # Claude가 GPT식으로 {"findings": [...]}처럼 최상위를 객체로
                # 감싸서 응답할 가능성에 대비한다.
                raise TypeError("응답이 JSON 배열이 아님")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Claude 응답이 JSON 배열이 아님: {text[:200]}") from exc

    async def _call_object(self, system: str, user: str) -> dict:
        response = await self._sdk_client.messages.create(
            model=self._model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = self._extract_text(response)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise TypeError("응답이 JSON 객체가 아님")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Claude 응답이 JSON 객체가 아님: {text[:200]}") from exc

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               characters: List[dict], relationships: List[dict],
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        language = profile.get("language") or "대상언어"
        variant = profile.get("variant")
        language_label = f"{language}({variant})" if variant else language
        grammar_instruction = (profile.get("grammar_agreement") or {}).get("llm_instruction", "")
        register_instruction = (profile.get("register_system") or {}).get("llm_instruction", "")

        system = (
            f"너는 한국어-{language_label} 자막의 1차 교정자다. 다음 항목만 직접 "
            "고쳐서 다시 써라: (1) 사전에 없는 애매한 비속어, (2) 글로서리에 없는 "
            "새 인물 이름의 표기 통일, (3) 확정된 인물 성별에 맞는 형용사/과거분사 "
            "일치, (4) 확정된 화자-청자 관계의 존댓말/반말 일관성. "
            "번역 품질 전반이나 로컬라이제이션은 다루지 마라 (2차 검수자의 몫). "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
        )
        if grammar_instruction:
            system += f"성별 일치 지침: {grammar_instruction}\n"
        if register_instruction:
            system += f"격식 지침: {register_instruction}\n"
        system += (
            f"확정된 인물 성별: {json.dumps(characters, ensure_ascii=False)}\n"
            f"확정된 관계 격식: {json.dumps(relationships, ensure_ascii=False)}\n"
            f"사전에 없어 애매한 비속어 후보: "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
            + _JSON_INSTRUCTION + "\n" + _PRIMARY_SCHEMA_INSTRUCTION
        )
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        return await self._call_array(system, user)

    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        system = (
            f"다음 자막 줄이 글자수 제약(줄당 {max_chars}자 이내, 최대 {max_lines}줄)을 "
            "위반했다. 의미를 최대한 보존하며 제약 안으로 줄여라.\n"
            + _SHRINK_SCHEMA_INSTRUCTION
        )
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        result = await self._call_object(system, text)
        return result["shrunk_text"]

    async def check_grammar_necessity(self, pairs: List[dict], profile: dict) -> List[dict]:
        language = profile.get("language") or "대상언어"
        variant = profile.get("variant")
        language_label = f"{language}({variant})" if variant else language
        system = (
            f"다음은 {language_label} 자막 줄 목록이다. 각 줄이 문법적으로 "
            "성별 일치나 존댓말/반말 판단이 필요한 줄인지만 순수하게 문법적으로 "
            "판단하라 — 누가 말했는지, 맥락이 무엇인지는 몰라도 된다. 그 줄 "
            "텍스트 자체에 성별 표시 형용사/과거분사가 있는지, 대화체 문장인지만 "
            "보고 판단하라.\n" + _GRAMMAR_NECESSITY_SCHEMA_INSTRUCTION
        )
        user = json.dumps(pairs, ensure_ascii=False)
        response = await self._sdk_client.messages.create(
            model=self._model, max_tokens=4096, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = self._extract_text(response)
        try:
            parsed = json.loads(text)
            # 스키마 지침은 {"results": [...]} 객체를 요구하지만, Claude가
            # 배열을 바로 반환하는 경우도 허용한다(둘 다 유효한 응답으로 취급).
            results = parsed if isinstance(parsed, list) else parsed["results"]
            if not isinstance(results, list):
                raise TypeError("results가 리스트가 아님")
            return results
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"문법 필요성 판단 응답이 기대한 형태가 아님: {preview}") from exc
