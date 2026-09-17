from datetime import datetime

import pytest

from app.core.export import (
    assemble_final_srt, compute_stats, safety_net_check, build_export_filename,
    glossary_consistency_check,
)
from app.providers.mock import MockProvider


def test_build_export_filename_without_episode():
    assert build_export_filename("보통의 연애", None, "es", "LATAM") == "보통의 연애_es_LATAM.srt"


def test_build_export_filename_with_episode_uses_hoi_suffix():
    assert build_export_filename("보통의 연애", 3, "es", "LATAM") == "보통의 연애_3화_es_LATAM.srt"


def test_build_export_filename_strips_filesystem_unsafe_characters():
    assert build_export_filename('제목: "특별판"/1', None, "es", "LATAM") == "제목 특별판1_es_LATAM.srt"


def test_assemble_final_srt_uses_final_text_for_approved_findings():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "texto malo"}]
    findings = [{"segment_id": "p1", "status": "approved", "final_text": "texto bueno"}]
    srt = assemble_final_srt(segments, findings)
    assert "texto bueno" in srt
    assert "texto malo" not in srt


def test_assemble_final_srt_keeps_original_when_rejected():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "texto original"}]
    findings = [{"segment_id": "p1", "status": "rejected", "final_text": ""}]
    srt = assemble_final_srt(segments, findings)
    assert "texto original" in srt


def test_assemble_final_srt_skips_segments_with_empty_target_text():
    """정렬 실패로 대상언어 텍스트가 없는 한국어 전용 세그먼트는 빈 자막 큐가
    되어선 안 된다."""
    segments = [
        {"id": "p1", "start": 0.0, "end": 2.0, "text": "hola"},
        {"id": "p2", "start": 2.0, "end": 4.0, "text": ""},
        {"id": "p3", "start": 4.0, "end": 6.0, "text": "adiós"},
    ]
    srt = assemble_final_srt(segments, [])
    assert "hola" in srt and "adiós" in srt
    # 큐 번호는 2개만, 그리고 빈 텍스트 큐가 없어야 한다.
    assert srt.count("-->") == 2
    assert "00:00:02,000 --> 00:00:04,000" not in srt


def test_assemble_final_srt_orders_by_start_time_not_insertion_order():
    """align()이 짝 없는 대상언어 세그먼트를 뒤에 붙이므로 저장 순서는 시간
    순서와 다를 수 있다."""
    segments = [
        {"id": "p1", "start": 10.0, "end": 12.0, "text": "tercero"},
        {"id": "p2", "start": 0.0, "end": 2.0, "text": "primero"},
        {"id": "p3", "start": 5.0, "end": 7.0, "text": "segundo"},
    ]
    srt = assemble_final_srt(segments, [])
    assert srt.index("primero") < srt.index("segundo") < srt.index("tercero")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000\nprimero")


def test_compute_stats_calculates_reflection_rate():
    findings = [
        {"status": "approved"}, {"status": "rejected"}, {"status": "modified"},
        {"status": "pending"},
    ]
    stats = compute_stats(findings)
    assert stats.finding_count == 4
    assert stats.reflection_rate == 0.5  # approved+modified = 2/4


# 자동보정 finding은 save_pipeline_result가 만들며 reviewed_at이 NULL이다.
# 검수자가 손댄 finding은 review-action이 reviewed_at을 채운다.
_AUTO_ELLIPSIS_FIX = {"segment_id": "p1", "status": "approved",
                      "final_text": "BAD aquí...", "source": "rule",
                      "reviewed_at": None}


def test_reviewer_decision_beats_auto_applied_rule_fix_on_same_segment():
    """자동보정된 온점 위반(저장 시점에 이미 approved, 미검수)과 검수자가 승인한
    LLM 오역 수정이 같은 세그먼트를 가리킬 수 있다. 기계적 자동보정이 검수자
    판단을 덮어써선 안 되며, 결과가 finding 순서(=DB 행 반환 순서)에 좌우돼서도
    안 된다."""
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "BAD aquí..."}]
    reviewer_fix = {"segment_id": "p1", "status": "approved",
                    "final_text": "texto corregido", "source": "llm",
                    "reviewed_at": datetime(2026, 7, 27, 12, 0)}

    for findings in ([_AUTO_ELLIPSIS_FIX, reviewer_fix],
                     [reviewer_fix, _AUTO_ELLIPSIS_FIX]):
        srt = assemble_final_srt(segments, findings)
        assert "texto corregido" in srt
        assert "BAD aquí..." not in srt


def test_reviewer_modified_rule_finding_beats_auto_applied_rule_fix():
    """rule 대 rule 충돌. 같은 세그먼트에 자동보정된 온점 위반과, 검수자가
    직접 고친(modified) 줄 길이 위반이 함께 걸릴 수 있다 — 둘 다 source="rule"
    이다(source는 finding을 '누가 만들었는지'일 뿐 '누가 해결했는지'가 아니라서,
    검수자가 고쳐도 "rule"로 남는다). 검수자의 수정이 반드시 이겨야 한다."""
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "BAD aquí..."}]
    reviewer_modified = {"segment_id": "p1", "status": "modified",
                         "final_text": "texto acortado", "source": "rule",
                         "reviewed_at": datetime(2026, 7, 27, 12, 0)}

    for findings in ([_AUTO_ELLIPSIS_FIX, reviewer_modified],
                     [reviewer_modified, _AUTO_ELLIPSIS_FIX]):
        srt = assemble_final_srt(segments, findings)
        assert "texto acortado" in srt
        assert "BAD aquí..." not in srt


def test_assemble_final_srt_skips_excluded_segments():
    segments = [
        {"id": "p1", "start": 0.0, "end": 2.0, "text": "hola", "excluded": False},
        {"id": "p2", "start": 2.0, "end": 4.0, "text": "texto sin coreano", "excluded": True},
    ]
    srt = assemble_final_srt(segments, [])
    assert "hola" in srt
    assert "texto sin coreano" not in srt
    assert srt.count("-->") == 1


def test_assemble_final_srt_includes_segment_when_excluded_is_false_or_missing():
    """excluded 필드가 아예 없는(레거시) segment dict도 안전하게 처리해야
    한다 — .get()으로 접근하므로 KeyError 없이 기본적으로 포함된다."""
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "hola"}]
    srt = assemble_final_srt(segments, [])
    assert "hola" in srt


def test_safety_net_check_skips_excluded_segments():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "a" * 100, "excluded": True}]
    violations = safety_net_check(segments, [])
    assert violations == []


# --- glossary_consistency_check: 2단계 판정(문자열 매칭 후보 → LLM 필터).
# MockProvider.check_glossary_reflection은 korean_text에 "대명사"가 있으면
# 정당한 대체로 보고 걸러낸다(violation=False).

_GLOSSARY_ENTRIES = [{"entry_id": "e1", "korean_term": "강오크", "canonical": "Kang-ok"}]


@pytest.mark.asyncio
async def test_glossary_consistency_check_skips_llm_call_when_no_candidates():
    """후보가 없으면(문자열 매칭에서부터 걸리는 게 없으면) provider를
    아예 부르지 않는다 — None을 넘겨도 에러가 나지 않아야 한다."""
    violations = await glossary_consistency_check([], [], [], None, {})
    assert violations == []


@pytest.mark.asyncio
async def test_glossary_consistency_check_keeps_genuine_mismatch():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "Oye, Gang-ok.",
                 "korean_text": "야, 강오크."}]
    violations = await glossary_consistency_check(
        segments, [], _GLOSSARY_ENTRIES, MockProvider(), {})
    assert len(violations) == 1
    assert violations[0].rule == "glossary_mismatch"
    assert violations[0].segment_id == "p1"


@pytest.mark.asyncio
async def test_glossary_consistency_check_filters_out_legitimate_pronoun_substitution():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "Oye, él.",
                 "korean_text": "야, 강오크(대명사로 지칭됨)."}]
    violations = await glossary_consistency_check(
        segments, [], _GLOSSARY_ENTRIES, MockProvider(), {})
    assert violations == []


@pytest.mark.asyncio
async def test_glossary_consistency_check_falls_back_to_violation_when_llm_fails():
    """LLM 판정이 실패해도 후보를 조용히 누락시키지 않는다 — 과탐지
    허용, 누락 금지."""
    class _FailingProvider(MockProvider):
        async def check_glossary_reflection(self, items, profile):
            raise RuntimeError("boom")

    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "Oye, Gang-ok.",
                 "korean_text": "야, 강오크."}]
    violations = await glossary_consistency_check(
        segments, [], _GLOSSARY_ENTRIES, _FailingProvider(), {})
    assert len(violations) == 1


@pytest.mark.asyncio
async def test_glossary_consistency_check_surfaces_matched_text_in_detail():
    """LLM이 matched_text(실제로 쓰인 다른 표기)를 돌려주면, 등록 표기와
    나란히 비교해 보여준다 — 검수자가 대본을 직접 열어보지 않고도 "무엇이
    무엇으로 바뀌었는지" 바로 알 수 있어야 한다."""
    class _AbbreviationProvider(MockProvider):
        async def check_glossary_reflection(self, items, profile):
            return [{"id": i["id"], "violation": True, "matched_text": "EE.UU.",
                     "matched_meaning": "미국"} for i in items]

    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "irás a la universidad en EE.UU.",
                 "korean_text": "미국에서 대학도 다니고"}]
    entries = [{"entry_id": "e1", "korean_term": "미국", "canonical": "Estados Unidos"}]
    violations = await glossary_consistency_check(
        segments, [], entries, _AbbreviationProvider(), {})
    assert len(violations) == 1
    assert violations[0].matched_text == "EE.UU."
    assert violations[0].matched_meaning == "미국"
    assert violations[0].detail == "'미국' 등록 표기 'Estados Unidos' → 최종 텍스트 'EE.UU.'(미국)"


@pytest.mark.asyncio
async def test_glossary_consistency_check_surfaces_text_gloss_when_no_trace_found():
    """matched_text조차 없이 흔적 없이 사라진 경우, LLM이 돌려준 text_gloss
    (최종 텍스트 전체의 한국어 요약)를 그대로 실어 보낸다 — 검수자가
    대상언어를 몰라도 그 줄에 실제로 뭐라고 쓰여 있는지 알 수 있어야 한다."""
    class _NoTraceProvider(MockProvider):
        async def check_glossary_reflection(self, items, profile):
            return [{"id": i["id"], "violation": True, "matched_text": "",
                     "matched_meaning": "",
                     "text_gloss": "그냥 비행기를 타고 있을 거라고만 되어 있음"} for i in items]

    segments = [{"id": "p1", "start": 0.0, "end": 2.0,
                 "text": "Mi prometida debe estar subiendo al avión,",
                 "korean_text": "내 약혼녀가 곧 미국행 비행기를 타거나"}]
    entries = [{"entry_id": "e1", "korean_term": "미국", "canonical": "Estados Unidos"}]
    violations = await glossary_consistency_check(
        segments, [], entries, _NoTraceProvider(), {})
    assert len(violations) == 1
    assert violations[0].matched_text == ""
    assert violations[0].text_gloss == "그냥 비행기를 타고 있을 거라고만 되어 있음"
