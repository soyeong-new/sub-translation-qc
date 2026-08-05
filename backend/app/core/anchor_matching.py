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


def find_relationship_anchor_candidates(scene: List[AlignedPair], relationships: List[dict]) -> List[dict]:
    """씬 텍스트에 관계의 화자·상대 라벨이 **둘 다** 명시적으로 등장하는 관계만
    후보로 태깅한다. gender와 달리 한쪽 이름만으로는 두 사람 사이의 관계를
    특정할 근거가 부족하므로(예: "민지"만 나오면 민지가 등장하는 여러 관계 중
    어느 것인지 알 수 없다), 화자/상대 라벨이 모두 나온 관계만 후보로 삼는다."""
    text = _scene_text(scene)
    candidates = []
    for rel in relationships:
        speaker_label = rel.get("speaker_label")
        addressee_label = rel.get("addressee_label")
        if speaker_label and addressee_label and speaker_label in text and addressee_label in text:
            candidates.append({"id": rel["id"], "label": f"{speaker_label} → {addressee_label}"})
    return candidates
