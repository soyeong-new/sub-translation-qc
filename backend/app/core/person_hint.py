"""성별 확인 카드에서 "사람이 아닐 가능성이 높은" 그룹을 미리 알려 주는 판정.

LLM이 후보 단어를 "사람(is_person=true)"으로 판단하면서도 대상 설명(referent)에는 "상황"·"게임"처럼
사람이 아닌 대상을 적는 모순 응답이 실제로 나온다(썰 운영 실행에서 15건). 그런 그룹을 걸러 없애면
진짜 사람의 성별 확인을 건너뛸 수 있어서(저장된 사람 확정 그룹 1,710개 중 3개가 잘못 걸림) 카드는
그대로 두고, 화면이 "사람 아님" 버튼을 미리 강조하게만 한다 — 확정은 여전히 검수자가 누른다.

규칙: referent에 비사람 키워드가 있고 사람 단서가 없을 때. 사람 단서가 있는 경우("화자 자신의 상황",
"상황을 평가하는 화자")는 화자의 성별에 일치하는 형용사인 경우가 많아 제외한다."""

from typing import Optional

NON_PERSON_KEYWORDS = ("상황", "분위기", "게임", "사물", "상태")
PERSON_CUES = ("화자", "나", "듣는", "상대", "사람", "인물", "청자", "본인", "자신", "남성", "여성", "그", "그녀", "사장", "회장")


def likely_not_person(referent: Optional[str]) -> bool:
    if not referent:
        return False
    if not any(k in referent for k in NON_PERSON_KEYWORDS):
        return False
    return not any(c in referent for c in PERSON_CUES)
