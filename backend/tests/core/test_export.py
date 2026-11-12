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


def test_assemble_final_srt_restores_original_cues_of_merged_segment():
    """정렬이 합친 큐는 고친 내용이 없으면 원래 대상언어 큐 나눔으로 나가고,
    고쳤거나 시간을 바꿨으면 합친 큐 하나로 나간다."""
    cues = [{"start": 0.0, "end": 1.0, "text": "Hola, señor."},
            {"start": 1.2, "end": 3.0, "text": "-Soy el mayordomo."}]
    merged = {"id": "p1", "start": 0.0, "end": 3.0,
              "text": "Hola, señor. -Soy el mayordomo.", "target_cues": cues}

    srt = assemble_final_srt([merged], [])
    assert srt.count("-->") == 2
    assert "Hola, señor.\n" in srt

    edited = assemble_final_srt([merged], [{"segment_id": "p1", "status": "modified",
                                            "final_text": "Hola."}])
    assert edited.count("-->") == 1

    retimed = assemble_final_srt([{**merged, "end": 2.5}], [])
    assert retimed.count("-->") == 1

    # 격식 반영처럼 finding 없이 target_text만 바뀐 경우도 고친 것으로 본다.
    rewritten = assemble_final_srt([{**merged, "text": "Hola, señor. -Soy su mayordomo."}], [])
    assert rewritten.count("-->") == 1 and "su mayordomo" in rewritten


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


def test_unconfirmed_amount_check_flags_segment_using_the_term():
    from app.core.export import unconfirmed_amount_check
    segs = [{"id": "s1", "korean_text": "대신 100억", "excluded": False},
            {"id": "s2", "korean_text": "안녕", "excluded": False}]
    v = unconfirmed_amount_check(segs, ["100억"])
    assert [x.segment_id for x in v] == ["s1"]
    assert v[0].rule == "glossary_mismatch" and "100억" in v[0].detail


def test_unconfirmed_amount_check_ignores_longer_number_containing_the_term():
    from app.core.export import unconfirmed_amount_check
    segs = [{"id": "s1", "korean_text": "110억 짜리", "excluded": False}]
    assert unconfirmed_amount_check(segs, ["10억"]) == []


def test_unconfirmed_amount_check_skips_excluded_segments_and_empty_terms():
    from app.core.export import unconfirmed_amount_check
    segs = [{"id": "s1", "korean_text": "100억", "excluded": True}]
    assert unconfirmed_amount_check(segs, ["100억"]) == []
    assert unconfirmed_amount_check(
        [{"id": "s1", "korean_text": "x", "excluded": False}], []) == []


def test_assemble_final_srt_applies_profile_space_before_punct():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "Pourquoi?"}]
    assert "Pourquoi ?" in assemble_final_srt(segments, [], {"space_before_punct": "?!:;"})
    assert "Pourquoi?" in assemble_final_srt(segments, [], {})


def test_assemble_final_srt_applies_profile_ligatures():
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "Soeur, poème?"}]
    profile = {"ligature_words": ["sœur"], "space_before_punct": "?!:;"}
    srt = assemble_final_srt(segments, [], profile)
    assert "Sœur, poème ?" in srt


@pytest.mark.asyncio
async def test_glossary_consistency_check_reuses_cached_judgment():
    """같은 줄·같은 용어는 두 번째 검사에서 LLM을 다시 부르지 않고, 바뀐 줄만 묻는다."""
    from app.core import export as export_mod
    export_mod._GLOSSARY_JUDGMENT_CACHE.clear()
    calls = []

    class _CountingProvider(MockProvider):
        async def check_glossary_reflection(self, items, profile):
            calls.append([i["id"] for i in items])
            return await super().check_glossary_reflection(items, profile)

    seg = {"id": "p1", "start": 0.0, "end": 2.0, "text": "Oye, Gang-ok.", "korean_text": "야, 강오크."}
    first = await glossary_consistency_check([seg], [], _GLOSSARY_ENTRIES, _CountingProvider(), {})
    second = await glossary_consistency_check([seg], [], _GLOSSARY_ENTRIES, _CountingProvider(), {})
    assert len(calls) == 1 and len(first) == len(second) == 1
    changed = {**seg, "text": "Oye, Gang-Ok."}
    await glossary_consistency_check([changed], [], _GLOSSARY_ENTRIES, _CountingProvider(), {})
    assert len(calls) == 2


def test_glossary_candidates_skip_term_contained_in_longer_registered_term():
    from app.core.export import _find_glossary_candidates
    entries = [{"entry_id": "e1", "korean_term": "신화 그룹", "canonical": "Shinhwa Group"},
               {"entry_id": "e2", "korean_term": "신화", "canonical": "Shinhwa"}]
    seg = {"id": "p1", "start": 0.0, "end": 2.0, "text": "Sabordez-vous !", "korean_text": "신화 그룹 자폭하라!"}
    found = _find_glossary_candidates([seg], [], entries)
    assert [c["korean_term"] for c in found] == ["신화 그룹"]
    # 짧은 용어가 긴 용어 밖에서도 따로 나오면 그 자리는 계속 검사한다.
    seg2 = {**seg, "korean_text": "신화 그룹, 신화!"}
    assert {c["korean_term"] for c in _find_glossary_candidates([seg2], [], entries)} == {"신화 그룹", "신화"}


def test_applied_glossary_fix_wins_over_reviewer_finding_and_undo_restores():
    from datetime import datetime, timezone
    seg = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "original"}]
    reviewed = {"segment_id": "p1", "status": "modified", "final_text": "reviewer text",
                "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    fix = {"segment_id": "p1", "status": "approved", "final_text": "fixed text",
           "reviewed_at": None, "_fix": True}
    assert "fixed text" in assemble_final_srt(seg, [reviewed, fix])
    assert "reviewer text" in assemble_final_srt(seg, [reviewed])  # fix를 지우면(되돌리기) 검수 결과로 돌아간다


# ---- 반복 대사 일관성 ----
def _seg(i, ko, text, **kw):
    return {"id": i, "start": ord(i) * 20.0, "end": 1.0, "text": text, "korean_text": ko, "excluded": False} | kw


def test_repeated_line_mismatch_lists_group_with_differing_finals():
    from app.core.export import repeated_line_mismatches
    segs = [_seg("a", "（일봉）아빠는 팔 안 아파", "Papa n'a pas mal au bras"),
            _seg("b", "전혀 다른 대사가 여기에 있다", "Autre"),
            _seg("c", "아빠는 팔 안 아파!", "Papa n'a pas mal aux bras.")]
    groups = repeated_line_mismatches(segs, [])
    assert len(groups) == 1
    assert [m["segment_id"] for m in groups[0]["members"]] == ["a", "c"]
    assert groups[0]["members"][0]["text"] == "Papa n'a pas mal au bras"


def test_repeated_line_same_text_ignoring_case_and_punctuation_is_not_listed():
    from app.core.export import repeated_line_mismatches
    segs = [_seg("a", "아빠는 팔 안 아파", "Papa n'a pas mal !"), _seg("c", "아빠는 팔 안 아파", "papa n'a pas mal")]
    assert repeated_line_mismatches(segs, []) == []


def test_repeated_line_uses_reviewer_final_text():
    from app.core.export import repeated_line_mismatches
    segs = [_seg("a", "아빠는 팔 안 아파", "Papa a mal"), _seg("c", "아빠는 팔 안 아파", "Papa n'a pas mal")]
    findings = [{"segment_id": "a", "status": "approved", "final_text": "Papa n'a pas mal", "reviewed_at": 1}]
    assert repeated_line_mismatches(segs, findings) == []


def test_repeated_line_ignores_excluded_and_empty_targets():
    from app.core.export import repeated_line_mismatches
    segs = [_seg("a", "아빠는 팔 안 아파", "Papa a mal", excluded=True), _seg("c", "아빠는 팔 안 아파", "Papa n'a pas mal"),
            _seg("d", "아빠는 팔 안 아파", "")]
    assert repeated_line_mismatches(segs, []) == []


def test_repeated_line_within_10_seconds_is_not_listed():
    from app.core.export import repeated_line_mismatches
    segs = [_seg("a", "아빠는 팔 안 아파", "Papa a mal", start=100.0), _seg("c", "아빠는 팔 안 아파", "Papa n'a pas mal", start=109.0)]
    assert repeated_line_mismatches(segs, []) == []
    segs[1]["start"] = 110.0
    assert len(repeated_line_mismatches(segs, [])) == 1
