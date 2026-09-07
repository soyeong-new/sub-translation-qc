"""Claude API로 1차 교정(rewrite)과 안전망 축약을 수행하는 얇은 SDK 래퍼."""

import json
import re
from typing import List
from anthropic import AsyncAnthropic
from app.providers.base import (
    contains_hangul, CATEGORY_ENUM, VERIFICATION_PRIORITY_PARAGRAPH,
    BATCH_SCOPE_INTRO, BATCH_SKIP_CLEAN_LINE, REQUERY_SCOPE_INTRO, REQUERY_SKIP_CLEAN_LINE,
    build_verification_checklist, build_json_instruction, build_json_instruction_requery,
    build_findings_schema_instruction, build_naturalness_instruction_line,
    build_improvement_judgment_criteria,
)

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


# envelope_declaration: claude는 JSON 배열 형태를 직접 출력한다(gpt는
# {"findings": [...]} 객체로 감싸야 해서 선언 문장만 다르다).
_JSON_INSTRUCTION = build_json_instruction(
    "반드시 JSON 배열만 출력하라. 다른 설명 텍스트를 붙이지 마라. ")

# 재질문(extra_instruction 있음) 전용 — 위 _JSON_INSTRUCTION의 "빼라" 지시가
# 검수자가 이미 지적한 단건 재검토와 충돌해 빈 응답을 유발하므로, 형식 지시는
# 유지하되 스킵 지시만 "반드시 포함, 판단이 바뀌어도 배열에 남긴 채 결론만
# 갱신"으로 바꿔 끼운다.
_JSON_INSTRUCTION_REQUERY = build_json_instruction_requery(
    "반드시 JSON 배열만 출력하라. 다른 설명 텍스트를 붙이지 마라. ")

_PRIMARY_SCHEMA_INSTRUCTION = build_findings_schema_instruction(
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: ")

_PRIMARY_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORY_ENUM},
            "corrected_text": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["segment_id", "category", "corrected_text", "description"],
        "additionalProperties": False,
    },
}

# 재질문(extra_instruction 있음) 전용 — 검수자에게 보이는 역번역도 새
# corrected_text에 맞춰 갱신해야 하므로, 별도 교차모델 API 호출 대신 같은
# 응답에 back_translation 필드를 함께 요청한다.
_PRIMARY_OUTPUT_SCHEMA_REQUERY = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORY_ENUM},
            "corrected_text": {"type": "string"},
            "description": {"type": "string"},
            "back_translation": {"type": "string"},
        },
        "required": ["segment_id", "category", "corrected_text", "description", "back_translation"],
        "additionalProperties": False,
    },
}

_BACK_TRANSLATION_FIELD_INSTRUCTION = (
    "추가로 back_translation (문자열, corrected_text를 자연스러운 한국어로 "
    "역번역 — 대상언어를 모르는 검수자가 교정 결과를 이해할 수 있게) 키도 "
    "반드시 포함하라. back_translation의 문장 자체도 예외 없이 한국어로 써라."
)

_BACK_TRANSLATE_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "korean_text": {"type": "string"},
            "original_korean_text": {"type": "string"},
            "is_improvement": {"type": "boolean"},
        },
        "required": ["id", "korean_text", "original_korean_text", "is_improvement"],
        "additionalProperties": False,
    },
}

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
                           temperature: float = None, output_schema: dict = None) -> List[dict]:
        target_model = model or self._model
        kwargs = {"temperature": temperature} if temperature is not None else {}
        if output_schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": output_schema}}
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
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")

        # extra_instruction은 재질문(다시 질문하기, requery.py) 단건 호출만 채워
        # 보낸다 — 배치 검증(pipeline.py)은 항상 빈 문자열이다. 배치용 "애매하면
        # 배열에서 빼라" 지시가 재질문에도 그대로 남아있으면, 검수자가 이미 콕
        # 집은 줄인데도 모델이 "명백한 문제 아님"으로 판단해 빈 배열을 내고 제안이
        # 그대로 남는 문제가 있었다(회귀: 사용자 재현 — 재질문해도 반영이 안 됨).
        # 그래서 이 값의 유무로 "애매하면 스킵" vs "이미 지적됐으니 반드시 포함"
        # 두 지시를 통째로 바꿔 끼운다.
        if extra_instruction:
            scope_intro = REQUERY_SCOPE_INTRO
            skip_clean_line = REQUERY_SKIP_CLEAN_LINE
        else:
            scope_intro = BATCH_SCOPE_INTRO
            skip_clean_line = BATCH_SKIP_CLEAN_LINE

        system = (
            f"너는 한국어-{language_label} 자막의 전문 번역 검수자다. "
            f"korean_text(한국어 원문)를 절대 기준(Source of Truth)으로 삼아 target_text({language_label} 번역문)를 검증하라. "
            + scope_intro +
            VERIFICATION_PRIORITY_PARAGRAPH +
            build_verification_checklist(language_label, skip_clean_line) +
            f"⚠️ [자막 형태 및 글자수 절대 제약 - HARD CONSTRAINT]\n"
            f"- 모든 교정문(corrected_text)은 반드시 다음 제약을 엄격히 지켜서 작성하라: {format_constraint}\n"
            "- 각 줄의 글자수를 실제로 세어보고 제약 글자수를 초과하면 절/쉼표 경계에서 자연스럽게 줄바꿈(\\n)을 넣거나 표현을 다듬어 글자수 한도 내로 들어오게 작성하라.\n\n"
            f"참고 지식베이스: {knowledge}\n"
        )
        json_instruction = _JSON_INSTRUCTION_REQUERY if extra_instruction else _JSON_INSTRUCTION
        output_schema = _PRIMARY_OUTPUT_SCHEMA_REQUERY if extra_instruction else _PRIMARY_OUTPUT_SCHEMA
        schema_instruction = _PRIMARY_SCHEMA_INSTRUCTION
        if extra_instruction:
            schema_instruction += "\n" + _BACK_TRANSLATION_FIELD_INSTRUCTION
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        system += build_naturalness_instruction_line(naturalness_instruction)
        system += json_instruction + "\n" + schema_instruction

        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        results = await self._call_array(system, user, model=self._model, temperature=0,
                                          output_schema=output_schema)
        return await self._retry_hangul_leaks(
            results, pairs, system, language_label, model=self._model, output_schema=output_schema)

    async def _retry_hangul_leaks(self, results: List[dict], pairs: List[dict], system: str,
                                   language_label: str, model: str, output_schema: dict) -> List[dict]:
        """지시(prompt)만으로는 못 막는 사례(design 논의: 배치 처리 중 모델이
        다른 항목의 korean_text를 착각해 corrected_text에 그대로 옮긴 실측
        사례)를 막는 마지막 방어선 — 한국어가 새어나온 항목만 원본 pair를
        다시 보내 한 번 더 묻고, 그래도 안 고쳐지면 검수자가 알아보게
        description에 경고를 남긴다(조용히 버리지 않는다 — 이 프로젝트는
        누락보다 과탐지를 선호한다)."""
        leaked_ids = {r["segment_id"] for r in results if contains_hangul(r.get("corrected_text"))}
        if not leaked_ids:
            return results
        retry_pairs = [p for p in pairs if p["id"] in leaked_ids]
        retry_system = system + (
            f"\n⚠️ 방금 응답에서 다음 segment_id의 corrected_text에 한국어가 섞여 있었다 — "
            f"금지 사항이다. 아래 항목만 다시 교정하되 corrected_text는 반드시 {language_label}"
            f"로만 작성하라 (한국어 단어를 절대 포함하지 마라): {sorted(leaked_ids)}"
        )
        retry_user = json.dumps(retry_pairs, ensure_ascii=False)
        retried = await self._call_array(retry_system, retry_user, model=model,
                                          temperature=0, output_schema=output_schema)
        retried_by_id = {r["segment_id"]: r for r in retried}
        fixed = []
        for r in results:
            if r["segment_id"] not in leaked_ids:
                fixed.append(r)
                continue
            replacement = retried_by_id.get(r["segment_id"], r)
            if contains_hangul(replacement.get("corrected_text")):
                replacement = dict(replacement)
                replacement["description"] = (
                    f"[⚠️ AI가 {language_label} 대신 한국어로 응답함 — 직접 재확인 필요] "
                    f"{replacement['description']}"
                )
            fixed.append(replacement)
        return fixed

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
            "⚠️ 이 문장은 이미 검수를 통과한 최종 문장이다 — 지금 입력에 이미 "
            "반영되어 있는 성별 표시(형용사/분사/명사 어미)와 격식(존댓말/반말) "
            "형태를 절대 바꾸지 마라. 글자수만 줄이고, 문법적 성·격식은 입력 "
            "그대로 유지하라.\n"
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
            + build_improvement_judgment_criteria(language_label)
            + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call_array(system, user, model=self._light_model,
                                       output_schema=_BACK_TRANSLATE_OUTPUT_SCHEMA)

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
