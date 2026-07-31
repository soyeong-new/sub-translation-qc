"""GPT API로 번역검토/민감어 판단을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from openai import AsyncOpenAI

_JSON_INSTRUCTION = (
    '반드시 {"findings": [...]} 형태의 JSON 객체만 출력하라. '
    "findings 배열의 각 항목 스키마는 지시된 필드를 그대로 따른다."
)

_REVIEW_SCHEMA_INSTRUCTION = (
    "findings 배열의 각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력으로 준 pair의 "id"와 반드시 일치), '
    'category (문자열, 반드시 "translation" 또는 "localization" 중 하나), '
    "description (문자열, 한국어로 작성), "
    "suggested_text (문자열, 제안 번역문), "
    "confidence (0과 1 사이의 실수). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)

_SENSITIVITY_SCHEMA_INSTRUCTION = (
    "findings 배열의 각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열, 입력으로 준 pair의 "id"와 반드시 일치), '
    "description (문자열, 한국어로 작성), "
    'severity (문자열, 반드시 "high", "medium", "low" 중 하나). '
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라."
)


class GptClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
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
        # choices가 빈 리스트면 (모델이 응답을 생성하지 않은 경우)
        # response.choices[0]에서 바로 IndexError가 나므로, 의도한 ValueError로
        # 먼저 막아준다.
        if not response.choices:
            raise ValueError("GPT 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            parsed = json.loads(text)
            findings = parsed["findings"]
            if not isinstance(findings, list):
                # findings가 리스트가 아니면 (예: 모델이 {"findings": {...}}처럼
                # dict 하나만 돌려준 경우) 여기서 걸러내지 않으면 ensemble.py의
                # `for item in result:`에서 원치 않는 TypeError가 새어나가
                # 전체 job이 API 비용을 다 쓴 뒤 크래시한다. 다른 malformed-
                # response 가드와 동일한 스타일의 ValueError로 통일한다.
                raise TypeError("findings가 리스트가 아님")
            return findings
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # text가 None이면 (GPT가 거부 응답 등으로 콘텐츠를 생성하지 않은 경우)
            # text[:200]도 TypeError를 내므로, 여기서도 안전하게 처리한다.
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 응답이 기대한 JSON 형태가 아님: {preview}") from exc

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
