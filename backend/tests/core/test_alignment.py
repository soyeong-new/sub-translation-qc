import pytest
from app.schemas import SegmentText
from app.core.alignment import align, detect_global_offset, align_by_korean_cue


def test_align_matches_overlapping_segments():
    korean = [SegmentText(start=0.0, end=2.0, text="안녕")]
    target = [SegmentText(start=0.1, end=2.1, text="Hola")]
    pairs = align(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "안녕"
    assert pairs[0].target.text == "Hola"


def test_align_produces_unmatched_pair_when_no_overlap():
    korean = [SegmentText(start=0.0, end=1.0, text="안녕")]
    target = [SegmentText(start=50.0, end=51.0, text="Hola")]
    pairs = align(korean, target)
    assert len(pairs) == 2
    kinds = {(p.korean is not None, p.target is not None) for p in pairs}
    assert (True, False) in kinds
    assert (False, True) in kinds


def test_align_buckets_multiple_words_into_one_target_cue_by_midpoint():
    """핵심 회귀: 위스퍼 문장 경계와 SRT 큐 경계가 달라도(문장 하나가 여러
    단어로, 그 단어들의 타임코드가 SRT 큐 하나에 걸쳐 있어도) 그 큐의
    korean_text로 전부 모여야 한다 — 문장 대 문장 IoU 매칭이 아니라 단어를
    큐 시간 구간에 담는 방식이라야 이게 가능하다."""
    korean_words = [
        SegmentText(start=0.1, end=0.4, text="안녕"),
        SegmentText(start=0.5, end=0.9, text="하세요"),
    ]
    target = [SegmentText(start=0.0, end=2.0, text="Hola")]
    pairs = align(korean_words, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "안녕 하세요"
    assert pairs[0].korean.start == 0.1
    assert pairs[0].korean.end == 0.9
    assert pairs[0].target.text == "Hola"


def test_align_splits_words_across_two_target_cues_by_own_midpoint():
    """단어 하나하나가 자기 중점이 속한 큐로만 들어가야 한다 — 한 발화가
    두 SRT 큐에 걸쳐 있어도 단어 단위로는 정확히 나뉜다."""
    korean_words = [
        SegmentText(start=0.1, end=0.4, text="안녕"),
        SegmentText(start=1.6, end=1.9, text="하세요"),
    ]
    target = [
        SegmentText(start=0.0, end=1.0, text="Hola"),
        SegmentText(start=1.5, end=2.0, text="Como estas"),
    ]
    pairs = align(korean_words, target)
    assert len(pairs) == 2
    assert pairs[0].korean.text == "안녕"
    assert pairs[1].korean.text == "하세요"


def test_align_matches_by_overlap_even_when_midpoint_falls_outside_target():
    """회귀: 한국어 SRT 단어를 STT가 못 들어 큐 경계로 폴백할 때
    (stt_srt_matching의 cue-bound 폴백 경로) 큐에 단어가 하나뿐이면 그 단어
    타임코드가 큐 표시 구간 전체로 늘어난다 — 실제 발화보다 길게 잡히는
    경우가 흔해서, 중점(midpoint)이 대응하는 대상언어 큐 밖으로 새는 실제
    사례("맞죠?")를 재현한다. 겹침 기준이면 이런 경우도 붙어야 한다."""
    korean_words = [SegmentText(start=202.70, end=203.54, text="맞죠?")]
    target = [SegmentText(start=202.16, end=202.95, text="Eres tú.")]
    pairs = align(korean_words, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "맞죠?"
    assert pairs[0].target.text == "Eres tú."


def test_align_prefers_true_majority_coverage_over_iou_biased_shorter_cue():
    """회귀: IoU(교집합/합집합)로 겹침을 판단하면 후보 큐가 짧을수록
    유리해지는 왜곡이 생긴다 — 단어 시간의 40%(4초)가 겹치는 긴 큐보다
    30%(3초)만 겹치는 짧은 큐를 IoU는 더 높게 쳐준다(짧은 큐는 합집합이
    작아서 비율이 부풀려짐: 3/10=0.3 > 4/20=0.2). 실제 사례("광천역에"가
    긴 큐와 짧은 큐에 걸쳐 있었을 때 IoU가 짧은 큐를 78% 차이로 압도적
    승리시켰던 것)와 같은 구조다. 단어 자신의 시간 대비 커버리지로
    판단하면(진짜 더 많이 겹치는 쪽) 긴 큐가 이겨야 한다."""
    word = SegmentText(start=0.0, end=10.0, text="단어")
    more_overlap_long = SegmentText(start=-10.0, end=4.0, text="긴 큐")  # 겹침 4.0초(40%)
    less_overlap_short = SegmentText(start=7.0, end=10.0, text="짧은 큐")  # 겹침 3.0초(30%)
    pairs = align([word], [more_overlap_long, less_overlap_short])
    matched = next(p for p in pairs if p.korean is not None)
    assert matched.target.text == "긴 큐"


def test_align_groups_orphan_words_by_gap():
    """어느 큐에도 안 담긴 단어들은 간격 기준으로 묶여야 한다 — 가까운
    단어끼리는 한 발화로 합쳐지고, 간격이 벌어지면 별도 pair가 된다."""
    korean_words = [
        SegmentText(start=10.0, end=10.3, text="가까운"),
        SegmentText(start=10.4, end=10.7, text="단어"),
        SegmentText(start=20.0, end=20.3, text="멀리"),
        SegmentText(start=20.4, end=20.7, text="떨어진"),
    ]
    pairs = align(korean_words, [])
    assert len(pairs) == 2
    assert pairs[0].korean.text == "가까운 단어"
    assert pairs[0].target is None
    assert pairs[1].korean.text == "멀리 떨어진"
    assert pairs[1].target is None


def test_detect_global_offset_finds_constant_shift():
    """회귀: 영상 앞부분을 잘라 올리면(리캡/인트로 제거 등) 한국어 STT
    타임코드 전체가 대상언어 SRT보다 상수만큼 앞선다 — 실제로 이 문제를
    겪은 사례(잘라낸 길이만큼 정확히 55초 어긋남)를 재현한다."""
    target = [
        SegmentText(start=start, end=start + 5.0, text=f"t{i}")
        for i, start in enumerate([0.0, 13.0, 41.0, 68.0, 100.0, 155.0])
    ]
    # 진짜 대응하는 한국어 단어는 각 큐보다 55초 앞서 있다(그 큐 시작 +1초
    # 지점에 상당하는 실제 발화 시각 = 큐.start - 55 + 1).
    korean_words = [
        SegmentText(start=t.start - 55.0 + 1.0, end=t.start - 55.0 + 1.5, text=f"k{i}")
        for i, t in enumerate(target)
    ]
    offset = detect_global_offset(korean_words, target)
    # 큐가 5초 폭이라 "완벽히 같은 점수"를 내는 오프셋 구간이 존재한다(예:
    # 54.0도 55.0도 전부 단어를 큐 안에 넣음) — 탐색은 그 구간 중 하나를
    # 고르면 충분하다(정확히 55.0일 필요는 없음, 이후 align()이 정상 동작
    #하기만 하면 된다).
    assert offset == pytest.approx(55.0, abs=1.5)


def test_detect_global_offset_returns_zero_when_already_aligned():
    target = [
        SegmentText(start=start, end=start + 5.0, text=f"t{i}")
        for i, start in enumerate([0.0, 13.0, 41.0, 68.0, 100.0, 155.0])
    ]
    korean_words = [
        SegmentText(start=t.start + 1.0, end=t.start + 1.5, text=f"k{i}") for i, t in enumerate(target)
    ]
    assert detect_global_offset(korean_words, target) == 0.0


def test_detect_global_offset_ignores_coincidental_match_below_minimum():
    """타겟 구간이 하나뿐이면 어느 오프셋에서든 단어 하나 정도만 우연히
    걸릴 수 있다 — 이런 우연한 매칭 몇 개로 상수 오프셋이 있다고 오판하면
    안 된다(최소 매칭 개수 기준 미달)."""
    target = [SegmentText(start=1000.0, end=1001.0, text="x")]
    korean_words = [SegmentText(start=float(i), end=float(i) + 0.5, text=f"k{i}") for i in range(20)]
    assert detect_global_offset(korean_words, target) == 0.0


def test_detect_global_offset_returns_zero_for_empty_inputs():
    assert detect_global_offset([], []) == 0.0
    assert detect_global_offset([SegmentText(start=0.0, end=1.0, text="k")], []) == 0.0


def test_align_by_korean_cue_matches_one_to_one():
    korean = [SegmentText(start=0.0, end=2.0, text="안녕")]
    target = [SegmentText(start=0.1, end=2.1, text="Hola")]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "안녕"
    assert pairs[0].target.text == "Hola"


def test_align_by_korean_cue_merges_multiple_target_cues_into_one_korean_cue():
    """한국어 SRT 한 큐의 번역이 스페인어 큐 두 개로 나뉘어 있으면, 하나의
    Segment로 합쳐져야 한다(design §반대 상황) — 원래 한국어에서 한 문장
    이었던 게 스페인어 큐 경계 때문에 잘리는 문제를 막는다."""
    korean = [SegmentText(start=0.0, end=2.0, text="이게 다 너 때문이야 알아")]
    target = [
        SegmentText(start=0.0, end=1.0, text="Es todo tu culpa."),
        SegmentText(start=1.0, end=2.0, text="¿Lo sabías?"),
    ]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "이게 다 너 때문이야 알아"
    assert pairs[0].target.text == "Es todo tu culpa. ¿Lo sabías?"


def test_align_by_korean_cue_merges_multiple_korean_cues_into_one_target_cue():
    """스페인어 큐 하나가 한국어 큐 여러 개에 걸쳐 있으면, 한국어 쪽을 합쳐
    하나의 Segment로 만들어 스페인어 텍스트 중복을 막는다(design §핵심
    사례)."""
    korean = [
        SegmentText(start=0.0, end=1.0, text="어 그거?"),
        SegmentText(start=1.0, end=2.0, text="아 맞다"),
    ]
    target = [SegmentText(start=0.0, end=2.0, text="Ah, ¿eso? Ah, cierto.")]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "어 그거? 아 맞다"
    assert pairs[0].target.text == "Ah, ¿eso? Ah, cierto."


def test_align_by_korean_cue_leaves_korean_only_orphan_as_half_pair():
    """겹치는 스페인어 큐가 없는 한국어 큐(배경 잡담 등)는 target=None인
    반쪽짜리로 남는다."""
    korean = [SegmentText(start=0.0, end=1.0, text="배경 잡담")]
    target = [SegmentText(start=50.0, end=51.0, text="Hola")]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 2
    kinds = {(p.korean is not None, p.target is not None) for p in pairs}
    assert (True, False) in kinds
    assert (False, True) in kinds


def test_align_by_korean_cue_leaves_target_only_orphan_as_half_pair():
    """겹치는 한국어 큐가 없는 스페인어 큐(대응하는 한국어를 못 찾은 실제
    번역 줄)는 korean=None인 반쪽짜리로 남는다."""
    korean = [SegmentText(start=0.0, end=1.0, text="안녕")]
    target = [
        SegmentText(start=0.0, end=1.0, text="Hola"),
        SegmentText(start=50.0, end=51.0, text="Texto sin coreano"),
    ]
    pairs = align_by_korean_cue(korean, target)
    orphan = next(p for p in pairs if p.target and p.target.text == "Texto sin coreano")
    assert orphan.korean is None


def test_align_by_korean_cue_preserves_chronological_order():
    korean = [
        SegmentText(start=10.0, end=11.0, text="나중"),
        SegmentText(start=0.0, end=1.0, text="먼저"),
    ]
    target = [
        SegmentText(start=10.0, end=11.0, text="Después"),
        SegmentText(start=0.0, end=1.0, text="Primero"),
    ]
    pairs = align_by_korean_cue(korean, target)
    assert [p.target.text for p in pairs] == ["Primero", "Después"]


def test_align_by_korean_cue_ignores_negligible_boundary_touch():
    """경계가 아주 살짝(문턱값 미만) 스치는 정도는 진짜 겹침으로 보지 않는다
    — 타이밍 오차로 인접한 무관한 큐가 잘못 묶이는 걸 막는다."""
    korean = [SegmentText(start=0.0, end=1.02, text="첫줄")]
    target = [
        SegmentText(start=0.0, end=1.0, text="Primero"),
        SegmentText(start=1.0, end=2.0, text="Segundo"),
    ]
    pairs = align_by_korean_cue(korean, target)
    matched = next(p for p in pairs if p.korean is not None)
    assert matched.target.text == "Primero"


def test_align_by_korean_cue_merges_transitive_chain_with_no_single_hub_node():
    """Union-Find의 전이적 묶음이 실제로 필요한 케이스 — 한국어 큐 하나가
    스페인어 큐 전부와 직접 겹치는 "허브" 구조가 아니라, 사슬처럼 이어진
    경우다: K1↔T1, T1↔K2, K2↔T2. K1과 T2는 서로 직접 안 겹치지만, 겹침
    관계를 따라가면 전부 하나로 묶여야 한다 — 단순히 "각 한국어 큐가
    직접 겹치는 스페인어 큐만 모으는" 방식으로는 이 케이스를 놓친다."""
    korean = [
        SegmentText(start=0.0, end=1.5, text="K1"),
        SegmentText(start=2.0, end=3.5, text="K2"),
    ]
    target = [
        SegmentText(start=1.0, end=2.5, text="T1"),
        SegmentText(start=3.0, end=4.5, text="T2"),
    ]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "K1 K2"
    assert pairs[0].target.text == "T1 T2"


def test_align_by_korean_cue_returns_empty_for_empty_inputs():
    assert align_by_korean_cue([], []) == []


def test_align_by_korean_cue_merge_uses_max_end_not_last_items_end():
    """회귀: _merge_words가 마지막 원소의 end를 그대로 쓰면, 정렬 후 뒤에
    오는 항목의 구간이 앞 항목보다 짧을 때(겹치는 큐) end가 실제보다
    짧게 잘린다 — 병합된 구간 전체를 덮는 max(end)를 써야 한다."""
    korean = [SegmentText(start=0.0, end=10.0, text="긴 한국어")]
    target = [
        SegmentText(start=0.0, end=10.0, text="Largo"),
        SegmentText(start=1.0, end=2.0, text="Corto"),
    ]
    pairs = align_by_korean_cue(korean, target)
    assert len(pairs) == 1
    assert pairs[0].target.end == 10.0
