"""인물관계도 이미지에서 인물/관계를 추출하는 Claude vision 클라이언트.

기존 ModelProvider(claude_client.py/gpt_client.py)와 별개의 독립 클래스다 — vision
추출은 STT/번역 파이프라인이 항상 거치는 필수 단계가 아니라 title 생성 시 선택적으로만
쓰이는 기능이라, ModelProvider ABC에 추가하면 MockProvider/LiveModelProvider 양쪽 다
관계없는 메서드를 강제로 구현해야 한다."""

import base64
import json
import os
from anthropic import AsyncAnthropic
from app.providers.base import ProviderNotConfiguredError

_MEDIA_TYPE_BY_EXTENSION = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

_SYSTEM_PROMPT = (
    "다음은 드라마/영화의 인물관계도 이미지다. 이미지에서 읽을 수 있는 인물 이름과 "
    "인물 간 관계를 추출하라. 관계의 방향과 유형(예: 연인, 남매, 친구, 직장 상사)을 "
    "이미지의 화살표·선·텍스트에서 파악하라. 인물의 성별을 사진이나 명시적 텍스트로 "
    "확신할 수 있으면 male/female로 표시하고, 확신할 수 없으면 null로 두어라. "
    "관계도 확신할 수 없으면 포함하지 마라. "
    '반드시 {"characters": [...], "relationships": [...]} 형태의 JSON 객체만 출력하라. '
    'characters 각 항목: {"label": 문자열, "suggested_gender": "male"|"female"|null}. '
    'relationships 각 항목: {"speaker_label": 문자열, "addressee_label": 문자열, '
    '"relationship_type": 문자열|null}.'
)


class ChartVisionClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._sdk_client = AsyncAnthropic(api_key=api_key)

    async def extract_chart(self, image_path: str) -> dict:
        ext = "." + image_path.rsplit(".", 1)[-1].lower()
        media_type = _MEDIA_TYPE_BY_EXTENSION.get(ext, "image/png")
        with open(image_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

        response = await self._sdk_client.messages.create(
            model=self._model, max_tokens=4096, system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": "이 인물관계도에서 인물과 관계를 추출해줘."},
                ],
            }],
        )
        if not response.content:
            raise ValueError("Claude 응답이 비어 있음")
        text = response.content[0].text
        try:
            parsed = json.loads(text)
            if "characters" not in parsed or "relationships" not in parsed:
                raise TypeError("characters/relationships 키가 없음")
            return parsed
        except (json.JSONDecodeError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"인물관계도 추출 응답이 기대한 형태가 아님: {preview}") from exc


def get_chart_vision_client() -> ChartVisionClient:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("CLAUDE_MODEL")
    if not api_key or not model:
        raise ProviderNotConfiguredError(
            "ANTHROPIC_API_KEY/CLAUDE_MODEL 환경변수가 설정되지 않았습니다.")
    return ChartVisionClient(api_key=api_key, model=model)
