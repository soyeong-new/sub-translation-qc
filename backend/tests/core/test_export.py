from app.core.export import assemble_final_srt, compute_stats


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


def test_reviewer_decision_beats_auto_applied_rule_fix_on_same_segment():
    """자동보정된 온점 위반(source="rule", 저장 시점에 이미 approved)과 검수자가
    승인한 오역 수정이 같은 세그먼트를 가리킬 수 있다. 기계적 자동보정이
    검수자 판단을 덮어써선 안 되며, 결과가 finding 순서(=DB 행 반환 순서)에
    좌우돼서도 안 된다."""
    segments = [{"id": "p1", "start": 0.0, "end": 2.0, "text": "BAD aquí..."}]
    rule_fix = {"segment_id": "p1", "status": "approved",
                "final_text": "BAD aquí...", "source": "rule"}
    reviewer_fix = {"segment_id": "p1", "status": "approved",
                    "final_text": "texto corregido", "source": "llm"}

    for findings in ([rule_fix, reviewer_fix], [reviewer_fix, rule_fix]):
        srt = assemble_final_srt(segments, findings)
        assert "texto corregido" in srt
        assert "BAD aquí..." not in srt
