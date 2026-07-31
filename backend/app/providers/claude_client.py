"""Claude API로 번역검토/민감어 판단을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from anthropic import AsyncAnthropic

_JSON_INSTRUCTION = "반드시 JSON 배열만 출력하라. 다른 설명 텍스트를 붙이지 마라."

_REVIEW_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력으로 준 pair의 "id"와 반드시 일치), '
    'category (문자열, 반드시 "translation" 또는 "localization" 중 하나), '
    "description (문자열, 한국어로 작성), "
    "suggested_text (문자열, 제안 번역문), "
    "confidence (0과 1 사이의 실수). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)

_SENSITIVITY_SCHEMA_INSTRUCTION = (
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력으로 준 pair의 "id"와 반드시 일치), '
    "description (문자열, 한국어로 작성), "
    'severity (문자열, 반드시 "high", "medium", "low" 중 하나). '
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)


class ClaudeClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._sdk_client = AsyncAnthropic(api_key=api_key)

    async def _call(self, system: str, user: str) -> List[dict]:
        response = await self._sdk_client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # content가 빈 리스트면 (모델이 콘텐츠 블록을 생성하지 않은 경우)
        # response.content[0]에서 바로 IndexError가 나므로, 의도한 ValueError로
        # 먼저 막아준다.
        if not response.content:
            raise ValueError("Claude 응답이 비어 있음")
        text = response.content[0].text
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                # 프롬프트에 정확한 필드명을 명시했더니, Claude가 GPT처럼
                # {"findings": [...]}로 감싸서 응답할 가능성이 생겼다 (GPT와
                # 달리 Claude는 최상위가 배열이어야 함). 여기서 걸러내지
                # 않으면 ensemble.py의 `for item in result:`가 dict의 키
                # (문자열)를 순회하며 `{**item, ...}`에서 TypeError로 새어나가
                # API 비용을 다 쓴 뒤 job이 크래시한다.
                raise TypeError("응답이 JSON 배열이 아님")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Claude 응답이 JSON 배열이 아님: {text[:200]}") from exc

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        system = (
            "너는 한국어-스페인어(LATAM) 자막 번역 검수자다. 오역/번역투/"
            "로컬라이제이션 문제를 찾아라. "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
            + _JSON_INSTRUCTION + "\n" + _REVIEW_SCHEMA_INSTRUCTION
        )
        user = json.dumps(pairs, ensure_ascii=False)
        return await self._call(system, user)

    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        system = (
            "다음은 민감어 사전에 걸린 후보 목록과 전체 문맥이다. 실제로 "
            "문제가 되는지 문맥을 보고 판단하라. severity는 high/medium/low 중 하나.\n"
            + _JSON_INSTRUCTION + "\n" + _SENSITIVITY_SCHEMA_INSTRUCTION
        )
        user = json.dumps({"pairs": pairs, "term_hits": term_hits}, ensure_ascii=False)
        return await self._call(system, user)
