"""Claude API로 1차 교정(rewrite)과 안전망 축약을 수행하는 얇은 SDK 래퍼."""

import json
import re
from typing import List
from anthropic import AsyncAnthropic

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")


def _strip_code_fence(text: str) -> str:
    """"반드시 JSON만 출력하라"고 지시해도 Claude는 종종 ```json ... ```
    코드펜스로 감싸거나 앞뒤에 설명 텍스트를 붙인다. 정규식으로 순수 JSON 영역만 안전하게 추출한다."""
    stripped = text.strip()
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', stripped, re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    json_match = re.search(r'(\[[\s\S]*\]|\{[\s\S]*\})', stripped)
    if json_match:
        return json_match.group(1).strip()

    return stripped


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
    "korean_text (문자열, text의 자연스러운 한국어 역번역), "
    "original_korean_text (문자열, original_text의 자연스러운 한국어 역번역 "
    "— 검수자가 교정 전 원문이 원래 무슨 뜻이었는지 비교할 수 있게), "
    "is_improvement (불리언, text가 original_text보다 reference_korean의 "
    "의미·톤을 더 잘 살리는 자연스러운 표현이면 true, 동등하거나 "
    "original_text가 더 낫다고 판단되면 false). "
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
    def __init__(self, api_key: str, model: str, light_model: str = None):
        self._model = model
        self._light_model = light_model or model
        self._sdk_client = AsyncAnthropic(api_key=api_key)

    def _extract_text(self, response) -> str:
        # Sonnet 5 이상은 thinking 파라미터를 안 주면 적응형 사고가 기본으로
        # 켜져, 복잡한 프롬프트에서 content[0]이 ThinkingBlock(.text 없음)일
        # 수 있다 — 반드시 type == "text"인 블록을 찾아서 읽어야 한다.
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise ValueError("Claude 응답에 텍스트 블록이 없음")

    async def _call_array(self, system: str, user: str, model: str = None,
                           temperature: float = None) -> List[dict]:
        target_model = model or self._model
        kwargs = {"temperature": temperature} if temperature is not None else {}
        response = await self._sdk_client.messages.create(
            model=target_model, max_tokens=8192, system=system,
            messages=[{"role": "user", "content": user}], **kwargs,
        )

        text = self._extract_text(response)
        try:
            parsed = json.loads(_strip_code_fence(text))
            if not isinstance(parsed, list):
                raise TypeError("응답이 JSON 배열이 아님")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Claude 응답이 JSON 배열이 아님: {text[:200]}") from exc

    async def _call_object(self, system: str, user: str, model: str = None) -> dict:
        target_model = model or self._model
        response = await self._sdk_client.messages.create(
            model=target_model, max_tokens=1024, system=system,
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
            f"너는 한국어-{language_label} 자막의 전문 번역 검수자다. "
            f"korean_text(한국어 원문)를 절대 기준(Source of Truth)으로 삼아 target_text({language_label} 번역문)를 검증하라. "
            "각 세그먼트를 먼저 전체적으로 읽고, 명백한 문제가 있다고 확신되는 경우에만 아래 [5단계 체크리스트]에서 해당하는 카테고리를 찾아 교정 사항(findings)을 작성하라. "
            "'혹시 여기도 어느 카테고리 하나쯤 해당되지 않을까' 하는 식으로 5개 카테고리를 억지로 하나씩 끼워 맞추려 하지 마라 — 명백한 문제가 없는 세그먼트는 그냥 건너뛰어라.\n\n"
            "⚠️ [우선순위] 아래 규칙들이 서로 충돌하면 이 순서를 따르라: "
            "정보 보존(고유명사·숫자·장소·행동 등 구체적 사실) > 오역/심의 정확성 > 씬 내 반복 표현 일관성 > 자연스러움. "
            "특히 자연스럽게 다듬는 과정에서 원문에 있는 구체적 사실을 생략·변경·추가하면 안 된다.\n\n"
            "⚠️ [검수 범위 및 교정 원칙]\n"
            "1. 반드시 교정해야 하는 대상:\n"
            "   - 오역 및 핵심 의미 누락/와전 (category: \"mistranslation\")\n"
            "   - 방송/미디어 심의 위반 비속어 (category: \"sensitivity\")\n"
            "   - 한국어 구조를 그대로 따라가 현지인이 읽기에 어색한 직역투 (category: \"unnatural_style\")\n"
            "   - 현지 문화권 관습, 관용구, 단위 표기 오류 (category: \"locale_convention\")\n"
            "   - 지정된 성별 및 격식(존댓말/반말) 파라미터 위반\n"
            "2. 교정 금지 대상 (취향 차이의 다듬기):\n"
            "   - 의미 왜곡이 없고 현지 구어체로 이미 타당한 번역인데, 단순히 AI 개인 선호 어휘나 동의어로 다듬는 수정은 제안하지 마라.\n"
            "   - 수정할 오류가 없는 깨끗한 문장은 절대 응답 배열에 포함하지 마라.\n"
            "   - nuance_tone(뉘앙스·어조)은 다음 경우에만 제안하라:\n"
            f"     * 직역투로 인해 명백히 어색한 경우 (한국어 구조를 그대로 따라가 {language_label}로서 부자연스러운 경우)\n"
            "     * 한국어 원문의 감정·톤(급함, 거침, 간결함, 여유로움 등)이 명확히 다르게 전달된 경우\n"
            "   - 이미 자연스러운 구어체 표현이면 건드리지 마라. 원문의 감정·톤을 정확히 전달하고 있으면 제안하지 마라.\n\n"
            "[5단계 순차 검증 체크리스트]\n"
            "1. 방송/미디어 심의 비속어 검수 (category: \"sensitivity\"):\n"
            "   - 기준: 영상 방영 및 미디어 심의(Broadcasting Rating)상 제재나 경고 대상이 될 수 있는 심한 비속어, 성적·인격모독적 표현이 포함되어 있는가?\n"
            "   - 교정 지침: 대사의 거친 뉘앙스는 유지하되, 방송 심의 기준에 적합한 수위가 약한 비속어나 자연스러운 순화 표현으로 교정(`corrected_text`)하라.\n"
            "2. 오역 및 핵심 의미 누락 (category: \"mistranslation\"):\n"
            "   - 기준: korean_text의 실제 의미와 target_text의 번역 의미가 다르게 와전되었거나, 문장의 핵심 의미가 생략되었는가?\n"
            "   - 교정 지침: 원문의 뜻을 왜곡 없이 정확하게 전달하도록 교정하라.\n"
            "3. 어색한 어조 및 직역투 (category: \"unnatural_style\" 또는 \"nuance_tone\"):\n"
            f"   - 기준: 문법은 맞지만 한국어 어순/표현을 그대로 따라간 직역투라 {language_label}로서 어색한가? 또는 한국어 원문의 감정·톤이 명확히 다르게 전달되었는가?\n"
            f"   - 교정 지침: 원문의 감정·톤을 정확히 살리면서 {language_label}권 현지인이 실제로 사용하는 자연스러운 구어체로 교정하라. 같은 씬 안에서 한국어 원문의 단어/표현이 반복되면, 문법적으로 다르게 써야 할 이유가 없는 한 같은 번역으로 통일하라.\n"
            "   - 주의: 원문이 이미 자연스러운 구어체로 한국어의 감정·톤을 잘 전달하고 있으면 nuance_tone 제안을 하지 마라.\n"
            "4. 문화 맥락 및 로컬라이제이션 (category: \"locale_convention\"):\n"
            f"   - 기준: {language_label}권 문화 관습, 관용 표현, 단위 표기(미터법/화폐 등)에 안 맞는 번역이 있는가?\n"
            "   - 교정 지침: 해당 언어권의 문화적 관습과 로컬라이제이션 관례에 맞게 교정하라.\n"
            "5. 성별 및 격식 지정 파라미터 준수:\n"
            "   - 기준: 각 입력 항목에 gender (male/female) 또는 formality (formal/informal) 값이 제공된 경우,\n"
            "   - 교정 지침: 교정된 문장(`corrected_text`)에서도 지정된 성별 표시와 격식(존댓말/반말)을 100% 완벽히 유지하여 작성하라.\n\n"
            f"⚠️ [자막 형태 및 글자수 절대 제약 - HARD CONSTRAINT]\n"
            f"- 모든 교정문(corrected_text)은 반드시 다음 제약을 엄격히 지켜서 작성하라: {format_constraint}\n"
            "- 각 줄의 글자수를 실제로 세어보고 제약 글자수를 초과하면 절/쉼표 경계에서 자연스럽게 줄바꿈(\\n)을 넣거나 표현을 다듬어 글자수 한도 내로 들어오게 작성하라.\n\n"
            f"참고 지식베이스: {knowledge}\n"
        )
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
            + _JSON_INSTRUCTION + "\n" + _PRIMARY_SCHEMA_INSTRUCTION
        )

        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        return await self._call_array(system, user, model=self._model, temperature=0)

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
        result = await self._call_object(system, text, model=self._light_model)
        return result["shrunk_text"]

    async def back_translate(self, texts: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(reference_korean), 교정 전 {language_label} 원본"
            f"(original_text), 교정 후 {language_label} 제안문(text) 목록이다. "
            "각 항목마다 두 가지를 하라.\n"
            "1. text와 original_text를 각각 자연스러운 한국어로 역번역하라"
            f"(korean_text, original_korean_text) — {language_label}를 모르는 검수자가 "
            "교정 전/후 의미를 나란히 비교하기 위한 참고용이므로, 의미뿐 "
            "아니라 톤·뉘앙스(간결함, 거침, 급함, 존중, 여유로움 등)도 함께 "
            "전달하라. 원문이 짧고 직설적이면 역번역도 짧고 직설적으로, "
            "원문에 존댓말·격식이 있으면 그 격식도 살려서 옮겨라 — 단순히 "
            "의미만 통하는 매끄러운 한국어 문장으로 다듬지 마라.\n"
            "2. text가 original_text보다 reference_korean의 의미·톤을 더 잘 "
            f"살리는 자연스러운 {language_label} 표현인지 판단하라"
            "(is_improvement). 의미 왜곡 없이 이미 자연스러운데 단순히 어휘 "
            "취향만 다르다면 개선으로 보지 마라 — 동등하면 false. text가 "
            "아무리 자연스러워도 reference_korean에 있는 구체적 정보(인물·"
            "장소·숫자·행동)를 생략·변경·추가했다면 무조건 false로 판정하라 "
            "— 자연스러움은 정보 보존을 앞설 수 없다.\n"
            + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call_array(system, user, model=self._light_model)

    async def check_equivalence(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과, 그걸 {language_label}로 교정한 "
            "두 후보 문구(text_a, text_b) 목록이다. 각 항목마다 text_a와 "
            "text_b가 같은 문제를 같은 방식으로 고친 것인지 판단하라.\n"
            + _EQUIVALENCE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call_array(system, user, model=self._light_model, temperature=0)
