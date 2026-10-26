from app.core.person_hint import likely_not_person


def test_flags_referents_that_describe_a_situation_or_thing():
    for referent in ("상황", "게임", "분위기", "게임 또는 상황", "상태"):
        assert likely_not_person(referent), referent


def test_does_not_flag_when_a_person_cue_is_present():
    """화자의 성별에 일치하는 형용사는 referent에 "상황"이 있어도 사람의 성별을 확인해야 한다
    (저장된 사람 확정 그룹: "화자 자신의 상황"→female, "상황을 평가하는 화자"→female)."""
    for referent in ("화자 자신의 상황", "상황을 평가하는 화자", "상황 또는 사람", "듣고 있는 상대방"):
        assert not likely_not_person(referent), referent


def test_does_not_flag_plain_person_referents_or_missing_referent():
    for referent in ("최덕팔", "회장님", "화자의 어머니", "", None):
        assert not likely_not_person(referent), referent
