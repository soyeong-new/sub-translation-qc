import pytest
from app.core.safety_net import shrink_violating_lines, enforce_line_length
from app.schemas import AlignedPair, SegmentText, FormatViolation, Finding
from app.providers.mock import MockProvider


def _pair(id_, text):
    return AlignedPair(id=id_, target=SegmentText(start=0.0, end=1.0, text=text))


@pytest.mark.asyncio
async def test_violation_merges_into_existing_approved_finding_instead_of_new_card(monkeypatch):
    """회귀(사용자 재현): Claude+GPT가 합의해 자동 승인된 finding의 문구가
    글자수 제약도 위반하면, 안전망이 새 카드를 또 만들면 안 된다 — 검수자
    눈엔 "방금 승인한 문장이랑 거의 똑같은 문장"이 카드 두 개로 보인다.
    기존 finding 하나를 그대로 갱신해서 카드가 하나만 남아야 한다."""
    long_text = "가" * 60
    pairs = [_pair("p1", long_text)]
    violations = [FormatViolation(segment_id="p1", rule="line_length", detail="60자")]
    # 프론트(splitDescription)가 "(원본 뜻 참고: ...)"를 문자열 끝에서
    # 정규식으로 잘라내 별도로 보여준다 — 여기 뒤에 뭔가 덧붙이면 그 파싱이
    # 깨진다(실제 사용자 재현 버그).
    original_description = "오역 교정 (원본 뜻 참고: 늦게 도착하면 두 배를 받는다는 뜻이다.)"
    existing = Finding(
        id="finding_p1_claude+gpt_mistranslation", target_version_id="tv1", segment_id="p1",
        category="mistranslation", description=original_description,
        original_text="원본", suggested_text=long_text, confidence=1.0,
        source="llm", model="claude+gpt", status="approved", final_text=long_text,
    )

    findings = await shrink_violating_lines(
        pairs, violations, MockProvider(), "tv1", existing_findings=[existing])

    assert findings == []  # 새 카드를 안 만듦
    assert len(existing.suggested_text) <= 50
    assert existing.final_text == existing.suggested_text
    assert existing.status == "approved"  # 기존 카드 그대로
    # description은 안 건드려서, "(원본 뜻 참고: ...)" 태그가 문자열 끝에
    # 그대로 남아 프론트 파싱이 안 깨져야 한다.
    assert existing.description == original_description


@pytest.mark.asyncio
async def test_no_violations_skips_llm_call_entirely():
    pairs = [_pair("p1", "짧은 줄")]
    findings = await shrink_violating_lines(pairs, [], MockProvider(), "tv1")
    assert findings == []
    assert pairs[0].target.text == "짧은 줄"


@pytest.mark.asyncio
async def test_violation_shrinks_text_and_updates_pair_in_place():
    long_text = "가" * 60
    pairs = [_pair("p1", long_text)]
    violations = [FormatViolation(segment_id="p1", rule="line_length", detail="60자")]
    findings = await shrink_violating_lines(pairs, violations, MockProvider(), "tv1")
    assert len(findings) == 1
    assert findings[0].model == "안전망"
    assert findings[0].status == "approved"
    assert pairs[0].target.text == findings[0].suggested_text
    assert len(pairs[0].target.text) <= 50


@pytest.mark.asyncio
async def test_violation_for_unknown_segment_id_is_skipped():
    pairs = [_pair("p1", "짧은 줄")]
    violations = [FormatViolation(segment_id="does-not-exist", rule="line_length", detail="x")]
    findings = await shrink_violating_lines(pairs, violations, MockProvider(), "tv1")
    assert findings == []


@pytest.mark.asyncio
async def test_violation_resolved_by_rewrap_alone_skips_llm():
    """내용 자체는 100자(50자x2줄) 안에 들어가고 그냥 한 줄에 몰려있어서
    위반이 난 경우, LLM을 부르지 않고 줄바꿈 재배치만으로 해결해야 한다."""
    text = " ".join(["word"] * 20)  # 99자, 한 줄
    pairs = [_pair("p1", text)]
    violations = [FormatViolation(segment_id="p1", rule="line_length", detail="1줄, 최대 줄 길이 99자")]

    provider = MockProvider()
    shrink_calls = []
    original_shrink_line = provider.shrink_line

    async def _spy_shrink_line(*args, **kwargs):
        shrink_calls.append(args)
        return await original_shrink_line(*args, **kwargs)
    provider.shrink_line = _spy_shrink_line

    findings = await shrink_violating_lines(pairs, violations, provider, "tv1")

    assert shrink_calls == []
    assert len(findings) == 1
    assert findings[0].model == "자동재배치"
    assert findings[0].source == "rule"
    lines = pairs[0].target.text.split("\n")
    assert len(lines) <= 2
    assert all(len(ln) <= 50 for ln in lines)


@pytest.mark.asyncio
async def test_enforce_line_length_leaves_short_text_untouched_without_calling_llm():
    provider = MockProvider()
    calls = []
    provider.shrink_line = lambda *a, **k: calls.append(a) or pytest.fail("should not be called")
    text, changed = await enforce_line_length("짧은 줄", provider)
    assert text == "짧은 줄"
    assert changed is False
    assert calls == []


@pytest.mark.asyncio
async def test_enforce_line_length_resolves_via_rewrap_without_llm():
    text = " ".join(["word"] * 20)  # 99자, 한 줄 — 재배치만으로 2줄x50자 안에 들어감
    result, changed = await enforce_line_length(text, MockProvider())
    assert changed is True
    lines = result.split("\n")
    assert len(lines) <= 2
    assert all(len(ln) <= 50 for ln in lines)


@pytest.mark.asyncio
async def test_enforce_line_length_falls_back_to_llm_when_rewrap_cannot_help():
    # 공백 없는 긴 단어 하나 — rewrap_line은 단어를 못 쪼개므로 항상 실패,
    # LLM(MockProvider.shrink_line = text[:max_chars]) 폴백을 강제로 탄다.
    text = "a" * 70
    result, changed = await enforce_line_length(text, MockProvider())
    assert changed is True
    assert len(result) <= 50
    assert result != text
