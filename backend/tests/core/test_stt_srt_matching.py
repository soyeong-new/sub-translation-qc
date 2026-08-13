import pytest
from app.core.stt_srt_matching import match_stt_words_to_korean_srt, merge_words_by_korean_cue


def test_match_uses_srt_punctuated_text_with_stt_real_timing(tmp_path):
    """SRT의 문장부호 포함 원문을 쓰되, 타이밍은 STT 실측값을 써야 한다 —
    SRT 큐 자체의 타임코드([5,7])가 아니라 STT가 실제로 들은 시각([0,2])이
    반영돼야 한다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:05,000 --> 00:00:07,000\n안녕하세요!\n", encoding="utf-8",
    )
    stt_words = [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    assert result == [{"start": 0.0, "end": 2.0, "text": "안녕하세요!", "cue_index": 0}]


def test_match_interpolates_word_stt_missed_between_confirmed_anchors(tmp_path):
    """STT가 중간 단어 하나를 놓쳐도, 그 단어는 양옆 확실한 매칭 지점
    사이의 좁은 구간에서 보간돼야 한다 — SRT 큐 전체 구간([0,10])이 아니라
    실제 확인된 앵커 사이([1.5,8.0])여야 정확하다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:10,000\n안녕 반가워 잘가\n", encoding="utf-8",
    )
    stt_words = [
        {"start": 1.0, "end": 1.5, "text": "안녕"},
        {"start": 8.0, "end": 8.5, "text": "잘가"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    texts = [w["text"] for w in result]
    assert texts == ["안녕", "반가워", "잘가"]
    middle = result[1]
    assert middle["text"] == "반가워"
    assert 1.5 <= middle["start"] < middle["end"] <= 8.0


def test_match_drops_stt_word_with_no_srt_counterpart(tmp_path):
    """SRT에 없는 STT 단어(오인식/추임새)는 결과에서 버려져야 한다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕\n", encoding="utf-8",
    )
    stt_words = [
        {"start": 0.0, "end": 0.2, "text": "어"},
        {"start": 0.5, "end": 1.0, "text": "안녕"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    assert result == [{"start": 0.5, "end": 1.0, "text": "안녕", "cue_index": 0}]


def test_match_falls_back_to_cue_bounds_when_no_confirmed_anchor_exists(tmp_path):
    """파일 전체에서 STT가 어떤 단어와도 안 겹치면(왼쪽도 오른쪽도 확실한
    앵커가 전혀 없는 i==0, j==n인 경우), 원본 SRT 큐 경계로 폴백해서
    보간해야 한다 — 큐 하나만 안 겹치는 경우가 아니라 파일 전체/이웃
    없음 케이스다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:10,000 --> 00:00:12,000\n안녕 친구\n", encoding="utf-8",
    )
    stt_words = [{"start": 50.0, "end": 50.5, "text": "완전히다른말"}]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    texts = [w["text"] for w in result]
    assert texts == ["안녕", "친구"]
    assert result[0]["start"] == pytest.approx(10.0)
    assert result[-1]["end"] == pytest.approx(12.0)


def test_match_returns_empty_when_stt_or_srt_has_no_words(tmp_path):
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n[음악]\n", encoding="utf-8",
    )
    assert match_stt_words_to_korean_srt(
        [{"start": 0.0, "end": 1.0, "text": "뭔가"}], str(srt_path)) == []
    assert match_stt_words_to_korean_srt([], str(srt_path)) == []


def test_match_drops_effect_and_song_lines(tmp_path):
    """정제 로직(이동됨) 회귀 — 효과음/노래 줄은 여전히 걸러져야 한다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n[효과음]\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n♪노래♪\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\n안녕 오랜만이야\n",
        encoding="utf-8",
    )
    stt_words = [
        {"start": 4.0, "end": 4.5, "text": "안녕"},
        {"start": 4.5, "end": 5.0, "text": "오랜만이야"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    texts = [w["text"] for w in result]
    assert texts == ["안녕", "오랜만이야"]


def test_match_strips_speaker_prefix_and_dash_multi_speaker(tmp_path):
    """정제 로직(이동됨) 회귀 — 화자 접두어/다중화자 "- " 처리가 여전히
    동작해야 한다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n(순모) 너무 걱정하지 마세요\n",
        encoding="utf-8",
    )
    stt_words = [
        {"start": 0.1, "end": 0.5, "text": "너무"},
        {"start": 0.5, "end": 1.0, "text": "걱정하지"},
        {"start": 1.0, "end": 1.5, "text": "마세요"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    texts = [w["text"] for w in result]
    assert texts == ["너무", "걱정하지", "마세요"]
    assert not any("순모" in t for t in texts)


def test_match_clamps_cue_fallback_instead_of_discarding_real_anchor_on_other_side(tmp_path):
    """회귀: 왼쪽에 확실한 앵커가 없어 큐 시작으로 대체할 때, 그 큐 시작이
    오른쪽의 실측 앵커보다 늦으면(SRT 큐 타임코드와 STT 실측이 어긋나는
    흔한 경우) 실측 앵커를 버리면 안 된다 — 큐 시작 쪽을 실측 앵커에
    맞춰 당겨야 한다."""
    srt_path = tmp_path / "ko.srt"
    # 이 큐의 SRT 타임코드는 10~14초라고 적혀 있지만, 실제 발화는
    # 그보다 일찍(8초) 시작했다고 가정한다.
    srt_path.write_text(
        "1\n00:00:10,000 --> 00:00:14,000\n안녕 반가워\n", encoding="utf-8",
    )
    stt_words = [
        # "안녕"은 STT가 못 들었다(왼쪽 앵커 없음).
        {"start": 8.0, "end": 8.5, "text": "반가워"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    texts = [w["text"] for w in result]
    assert texts == ["안녕", "반가워"]
    # "안녕"의 보간 구간은 실측 앵커(8.0)를 넘어서면 안 된다 — 큐 시작
    # (10.0)을 그대로 썼다면 8.0보다 늦어져서 다음 확정 구간과 겹친다.
    assert result[0]["end"] <= 8.0
    assert result[1]["start"] == 8.0


def test_match_keeps_multi_cue_gap_words_within_their_own_cue_bounds(tmp_path):
    """회귀: STT가 큐 여러 개를 통째로 놓치면(예: 음악/노이즈 구간), 그 사이
    단어들을 갭 전체 구간에 균등하게 뭉개면 안 된다 — 각 단어는 자기
    원래 큐의 시간대 안(또는 그 근처)에 있어야 한다. 안 그러면 align()이
    엉뚱한 대상언어 줄에 붙여버린다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n처음\n\n"
        "2\n00:01:00,000 --> 00:01:02,000\n중간에\n\n"
        "3\n00:01:30,000 --> 00:01:32,000\n놓친\n\n"
        "4\n00:02:00,000 --> 00:02:02,000\n마지막\n",
        encoding="utf-8",
    )
    # STT는 "처음"과 "마지막"만 듣는다 — "중간에"/"놓친"이 담긴 두 큐를
    # 통째로 놓친다.
    stt_words = [
        {"start": 1.0, "end": 1.5, "text": "처음"},
        {"start": 121.0, "end": 121.5, "text": "마지막"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    by_text = {w["text"]: w for w in result}
    # "중간에"는 원래 큐(60~62초) 근처여야지, 갭 전체(1.5~121.0초)에 걸쳐
    # 뭉개진 위치(예: 20초대)면 안 된다.
    assert 60.0 <= by_text["중간에"]["start"] <= 62.0
    # "놓친"은 원래 큐(90~92초) 근처여야 한다.
    assert 90.0 <= by_text["놓친"]["start"] <= 92.0
    # 시간 순서가 뒤집히면 안 된다(처음 < 중간에 < 놓친 < 마지막).
    assert (by_text["처음"]["end"] <= by_text["중간에"]["start"]
            <= by_text["중간에"]["end"] <= by_text["놓친"]["start"]
            <= by_text["놓친"]["end"] <= by_text["마지막"]["start"])


def test_match_assigns_increasing_cue_index_per_srt_cue(tmp_path):
    """서로 다른 SRT 큐에서 온 단어는 서로 다른 cue_index를 가져야 하고,
    같은 큐 안의 단어들은 같은 cue_index를 공유해야 한다 — 확정 매칭과
    보간 매칭 둘 다에서."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕 친구\n\n"
        "2\n00:00:10,000 --> 00:00:12,000\n반가워\n",
        encoding="utf-8",
    )
    stt_words = [
        {"start": 0.0, "end": 0.5, "text": "안녕"},
        {"start": 0.5, "end": 1.0, "text": "친구"},
        {"start": 10.0, "end": 10.5, "text": "반가워"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    by_text = {w["text"]: w["cue_index"] for w in result}
    assert by_text["안녕"] == by_text["친구"] == 0
    assert by_text["반가워"] == 1


def test_match_assigns_cue_index_to_interpolated_gap_words(tmp_path):
    """보간(interpolation)으로 채워진 단어도 자기 원래 큐의 cue_index를
    가져야 한다 — 확정 매칭 단어만 cue_index를 갖는 게 아니다."""
    srt_path = tmp_path / "ko.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:10,000\n안녕 반가워 잘가\n", encoding="utf-8",
    )
    stt_words = [
        {"start": 1.0, "end": 1.5, "text": "안녕"},
        {"start": 8.0, "end": 8.5, "text": "잘가"},
    ]
    result = match_stt_words_to_korean_srt(stt_words, str(srt_path))
    # "반가워"는 STT가 못 들어 보간된 단어다 — cue_index는 여전히 0이어야 한다.
    middle = next(w for w in result if w["text"] == "반가워")
    assert middle["cue_index"] == 0


def test_merge_words_by_korean_cue_groups_words_into_one_segment_per_cue():
    from app.core.stt_srt_matching import merge_words_by_korean_cue
    matched_words = [
        {"start": 0.0, "end": 0.5, "text": "안녕", "cue_index": 0},
        {"start": 0.5, "end": 1.0, "text": "친구", "cue_index": 0},
        {"start": 2.0, "end": 2.5, "text": "반가워", "cue_index": 1},
    ]
    cues = merge_words_by_korean_cue(matched_words)
    assert len(cues) == 2
    assert cues[0].start == 0.0
    assert cues[0].end == 1.0
    assert cues[0].text == "안녕 친구"
    assert cues[1].start == 2.0
    assert cues[1].end == 2.5
    assert cues[1].text == "반가워"


def test_merge_words_by_korean_cue_returns_empty_for_empty_input():
    from app.core.stt_srt_matching import merge_words_by_korean_cue
    assert merge_words_by_korean_cue([]) == []
