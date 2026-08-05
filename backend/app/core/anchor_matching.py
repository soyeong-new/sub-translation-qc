"""씬(연속된 세그먼트 묶음)의 한국어 텍스트에 title 로스터 인물의 이름이
명시적으로 등장하는지 확인하는 순수 함수 모듈. LLM을 쓰지 않는다 — 단순
부분 문자열 검색이며, "이 씬에 이 이름이 실제로 나왔는가"라는 좁고 신뢰할
수 있는 질문만 답한다. 로스터 인물이 씬에 등장하지 않았다고 판단하는 것도
아니고(다른 호칭으로 불렸을 수 있음), 씬의 등장인물을 전부 찾아내는 것도
아니다 — 명시적 이름 언급이 있는 경우만 후보로 태깅한다."""

from typing import List
from app.schemas import AlignedPair


def _scene_text(scene: List[AlignedPair]) -> str:
    """이름은 한국어 원문 기준으로 대조한다 — 로스터 라벨이 한국어 이름이고,
    대상언어 번역문에는 음역(예: "Minji")이 들어가 문자열이 다를 수 있다."""
    parts = []
    for pair in scene:
        if pair.korean is not None:
            parts.append(pair.korean.text)
    return " ".join(parts)


def find_anchor_candidates(scene: List[AlignedPair], roster: List[dict]) -> List[dict]:
    text = _scene_text(scene)
    return [character for character in roster if character.get("label") and character["label"] in text]
