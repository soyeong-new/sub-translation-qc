"""Claude API로 1차 교정(rewrite)과 안전망 축약을 수행하는 얇은 SDK 래퍼."""

import json
import re
from typing import List
from anthropic import AsyncAnthropic

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")


def _strip_code_fence(text: str) -> str:
    """"반드시 JSON만 출력하라"고 지시해도 Claude는 종종 ```json ... ```
    코드펜스로 감싸서 응답한다. json.loads가 그대로 실패하지 않도록 벗겨낸다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _CODE_FENCE_RE.sub("", stripped)
    return stripped.strip()

_JSON_INSTRUCTION = (
    "반드시 JSON 배열만 출력하라. 다른 설명 텍스트를 붙이지 마라. "
    "수정이 필요 없는 세그먼트는 배열에 포함하지 마라."
)

_PRIMARY_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력 pair의 "id"와 반드시 일치), '
    'category (문자열, 반드시 다음 중 하나: '
    '"sensitivity"(사전에 없어 애매한 비속어), '
    '"mistranslation"(의미가 잘못 옮겨졌거나 함축된 의미가 빠진 경우), '
    '"nuance_tone"(뉘앙스·어조가 원문과 다른 경우), '
    '"unnatural_style"(문법은 맞지만 한국어 구조를 그대로 따라간 직역투·어색한 흐름), '
    '"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현)), '
    "corrected_text (문자열, 교정된 전체 대상언어 텍스트), "
    "original_meaning (문자열, 지금 target_text(수정 전 원문)가 실제로 무슨 "
    "뜻인지 한국어로 간단히 설명 — \"~라는 뜻이다\"처럼. 대상언어를 모르는 "
    "검수자가 이 문장만 보고 원문이 뭘 말하는지 바로 알 수 있어야 한다), "
    "description (문자열, 무엇을 왜 그렇게 고쳤는지 한국어로 설명). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라. "
    "original_meaning과 description의 설명 문장 자체는 예외 없이 한국어로 "
    "써라 — 다른 언어로 설명하지 마라. 단, 대상언어 원문 표현을 예시로 "
    "인용하는 것은 괜찮다(예: \"'경비아저씨' 표현이 어색해 'el guardia'로 수정\")."
)

_SHRINK_SCHEMA_INSTRUCTION = (
    "정확히 다음 키를 가진 JSON 객체 하나만 출력하라: "
    "shrunk_text (문자열, 의미를 보존하며 글자수 제약 안으로 줄인 텍스트). "
    "다른 설명을 붙이지 마라."
)

_BACK_TRANSLATE_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "korean_text (문자열, 자연스러운 한국어 역번역). "
    "반드시 JSON 배열만 출력하라. 다른 설명을 붙이지 마라."
)

_EQUIVALENCE_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "equivalent (불리언, text_a와 text_b가 같은 문제를 같은 방식으로 고친 "
    "것이면 true, 단어 선택이 달라도 무방하다 — 실질적으로 다른 내용·뉘앙스·"
    "해결책이면 false). 반드시 JSON 배열만 출력하라. 다른 설명을 붙이지 마라."
)

def _language_label(profile: dict) -> str:
    language = profile.get("language") or "대상언어"
    variant = profile.get("variant")
    return f"{language}({variant})" if variant else language


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
            parsed = json.loads(_strip_code_fence(text))
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
            parsed = json.loads(_strip_code_fence(text))
            if not isinstance(parsed, dict):
                raise TypeError("응답이 JSON 객체가 아님")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Claude 응답이 JSON 객체가 아님: {text[:200]}") from exc

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        language_label = _language_label(profile)

        system = (
            f"너는 한국어-{language_label} 자막의 검증자다. korean_text(원문)와 "
            "target_text를 나란히 놓고 다음 기준으로 처음부터 독립적으로 검증·"
            "교정하라: 사전에 없는 애매한 비속어, 번역정확성, 문화맥락, 뉘앙스어조, "
            "자연스러운흐름(직역 지양, 한국어 어순을 그대로 따라간 부분을 찾아 "
            "고칠 것), 함축의미, 로컬라이제이션(그 나라 문화에 맞는 표현인지). "
            "성별 표시 형용사/분사의 형태나 격식(존댓말·반말)은 이미 앞 단계"
            "(파이썬의 결정론적 문법 반영 + 격식 전담 호출)에서 확정되어 "
            "target_text에 반영된 상태다 — 이 값을 판단하거나 새로 바꾸는 "
            "건 네 역할이 아니다. 다른 문제(오역/뉘앙스/직역투 등)를 고칠 "
            "때 이미 반영된 성별 형태와 격식을 절대 건드리지 마라(그대로 "
            "유지). 새 인물 이름의 표기 통일도 다루지 마라(별도 사전으로 "
            "관리). "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
        )
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
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
            "위반했다. 의미를 최대한 보존하며 제약 안으로 줄여라. 줄여도 한 줄에 "
            f"안 들어가 두 줄이 되면, 그 사이 줄바꿈(\\n)은 쉼표·접속사·절 경계처럼 "
            "자연스럽게 끊기는 지점에 넣어라 — 단어 중간을 자르거나 관사/전치사를 "
            "그 대상 명사와 떼어놓지 마라. 반드시 각 줄이 실제로 "
            f"{max_chars}자 이내인지, 줄 수가 {max_lines}줄 이내인지 다시 세어보고 "
            "확인한 뒤 출력하라.\n"
            + _SHRINK_SCHEMA_INSTRUCTION
        )
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        result = await self._call_object(system, text)
        return result["shrunk_text"]

    async def back_translate(self, texts: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 텍스트 목록이다. 각 항목을 자연스러운 "
            "한국어로 역번역하라 — 스페인어를 모르는 검수자가 원래 의미를 "
            "가늠하기 위한 참고용이므로, 의역보다 원문 의미를 최대한 그대로 "
            "전달하는 것을 우선하라.\n" + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call_array(system, user)

    async def check_equivalence(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과, 그걸 {language_label}로 교정한 "
            "두 후보 문구(text_a, text_b) 목록이다. 각 항목마다 text_a와 "
            "text_b가 같은 문제를 같은 방식으로 고친 것인지 판단하라.\n"
            + _EQUIVALENCE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call_array(system, user)
