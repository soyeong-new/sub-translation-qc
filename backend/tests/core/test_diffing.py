import pytest
from app.core.diffing import findings_from_corrections, apply_corrections
from app.schemas import AlignedPair, SegmentText


def _pair(id_, text):
    return AlignedPair(id=id_, target=SegmentText(start=0.0, end=1.0, text=text))


def test_findings_from_corrections_builds_finding_with_stage_as_model():
    pairs = [_pair("p1", "hola original")]
    corrections = [{"segment_id": "p1", "category": "translation",
                     "corrected_text": "hola corregido", "description": "오역 수정"}]
    findings = findings_from_corrections("tv1", pairs, corrections, stage="claude")
    assert len(findings) == 1
    assert findings[0].model == "claude"
    assert findings[0].original_text == "hola original"
    assert findings[0].suggested_text == "hola corregido"


def test_findings_from_corrections_skips_unchanged_text():
    pairs = [_pair("p1", "hola")]
    corrections = [{"segment_id": "p1", "category": "translation",
                     "corrected_text": "hola", "description": "변경 없음"}]
    assert findings_from_corrections("tv1", pairs, corrections, stage="gpt") == []


def test_findings_from_corrections_skips_duplicate_segment_id():
    pairs = [_pair("p1", "hola")]
    corrections = [
        {"segment_id": "p1", "category": "translation", "corrected_text": "a", "description": "1"},
        {"segment_id": "p1", "category": "translation", "corrected_text": "b", "description": "2"},
    ]
    findings = findings_from_corrections("tv1", pairs, corrections, stage="claude")
    assert len(findings) == 1
    assert findings[0].suggested_text == "a"


def test_findings_from_corrections_skips_unknown_segment_id():
    pairs = [_pair("p1", "hola")]
    corrections = [{"segment_id": "does-not-exist", "category": "translation",
                     "corrected_text": "x", "description": "y"}]
    assert findings_from_corrections("tv1", pairs, corrections, stage="claude") == []


def test_apply_corrections_mutates_pair_target_text_in_place():
    pairs = [_pair("p1", "original")]
    apply_corrections(pairs, [{"segment_id": "p1", "corrected_text": "updated"}])
    assert pairs[0].target.text == "updated"


def test_apply_corrections_leaves_unmatched_pairs_untouched():
    pairs = [_pair("p1", "original")]
    apply_corrections(pairs, [{"segment_id": "other", "corrected_text": "updated"}])
    assert pairs[0].target.text == "original"


def test_apply_corrections_uses_first_correction_for_duplicate_segment_id():
    """findings_from_corrections는 중복 segment_id에서 첫 번째 correction을
    채택한다(test_findings_from_corrections_skips_duplicate_segment_id 참고).
    apply_corrections가 다른 규칙(예: 마지막 값 우선)을 쓰면 검수자가 보는
    finding.suggested_text와 실제로 반영되는 pair.target.text가 서로 다른
    correction에서 나와 어긋난다 — 두 함수는 반드시 같은 correction을 골라야
    한다."""
    pairs = [_pair("p1", "hola")]
    corrections = [
        {"segment_id": "p1", "category": "translation", "corrected_text": "a", "description": "1"},
        {"segment_id": "p1", "category": "translation", "corrected_text": "b", "description": "2"},
    ]
    apply_corrections(pairs, corrections)
    assert pairs[0].target.text == "a"
