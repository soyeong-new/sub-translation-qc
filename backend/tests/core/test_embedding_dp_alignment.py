import pytest
from app.schemas import SegmentText
from app.providers.mock import MockProvider
from app.core.embedding_dp_alignment import (
    align_by_embedding_dp, cosine_similarity, _compute_overlap_ratio,
    _clean_text_for_embedding, _leading_speaker_tag, _is_all_caps_text,
)


def test_clean_text_for_embedding():
    assert _clean_text_for_embedding("[웃음] 안녕하세요") == "안녕하세요"
    assert _clean_text_for_embedding("화자 1: 반갑습니다") == "반갑습니다"
    assert _clean_text_for_embedding("[동석] (한숨) 정말 고마워") == "정말 고마워"
    assert _clean_text_for_embedding("김우택: 안녕하세요 [음악]") == "안녕하세요"


def test_cosine_similarity():

    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2), 0.001) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v3), 0.001) == 0.0


def test_compute_overlap_ratio():
    # 0~2, 1~3 -> overlap 1 second, union 3 seconds (IoU) -> ratio 1/3
    assert pytest.approx(_compute_overlap_ratio(0.0, 2.0, 1.0, 3.0), 0.001) == 1.0 / 3.0
    # No direct overlap, but 1 sec gap -> soft proximity score 0.2
    assert pytest.approx(_compute_overlap_ratio(0.0, 1.0, 2.0, 3.0), 0.001) == 0.2
    # Far gap (>3s) -> 0.0
    assert _compute_overlap_ratio(0.0, 1.0, 10.0, 11.0) == 0.0


def test_is_all_caps_text():
    assert _is_all_caps_text("TAL VEZ EL AMOR") is True
    assert _is_all_caps_text("¡FELICES 70 AÑOS,\nPROFESOR KIM DONG-JU!") is True
    assert _is_all_caps_text("Sí, señor.") is False
    assert _is_all_caps_text("El productor ejecutivo KIM Woo-taek") is False
    assert _is_all_caps_text("OK") is False  # 글자 3개 미만은 판단하지 않음
    assert _is_all_caps_text("") is False


def test_leading_speaker_tag():
    assert _leading_speaker_tag("(순모) 야") == "순모"
    assert _leading_speaker_tag("(투자자) 정 대표?") == "투자자"
    assert _leading_speaker_tag("저희 김 작가, 그") is None
    # 괄호가 맨 앞이 아니라 끝에 붙은 지문/효과음은 화자 표기로 취급하지 않는다
    assert _leading_speaker_tag("아, 가시게요?\n[순모의 어색한 웃음]") is None
    assert _leading_speaker_tag("") is None



@pytest.mark.asyncio
async def test_align_by_embedding_dp_basic_1to1():
    korean_cues = [
        SegmentText(start=0.0, end=2.0, text="안녕하세요"),
        SegmentText(start=2.5, end=4.0, text="반갑습니다"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=2.0, text="Hola"),
        SegmentText(start=2.5, end=4.0, text="Encantado"),
    ]
    provider = MockProvider()

    pairs = await align_by_embedding_dp(korean_cues, target_segments, provider)
    assert len(pairs) == 2
    assert pairs[0].korean.text == "안녕하세요"
    assert pairs[0].target.text == "Hola"
    assert pairs[1].korean.text == "반갑습니다"
    assert pairs[1].target.text == "Encantado"


@pytest.mark.asyncio
async def test_align_by_embedding_dp_blocks_merge_across_speaker_change():
    """화자 표기가 서로 다른 두 큐는(정제된 korean_cues에는 표기가 이미
    없더라도) korean_raw_cues로 원본을 넘기면 한 발화로 합쳐지지 않아야
    한다 — 화자 표기는 매칭 판단에만 쓰고 결과 텍스트에는 노출되지 않는다."""
    korean_cues = [
        SegmentText(start=0.0, end=1.0, text="안녕"),
        SegmentText(start=1.1, end=2.0, text="하세요 반갑습니다"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=2.0, text="Hola, mucho gusto"),
    ]
    provider = MockProvider()

    # 화자 표기가 없으면(또는 안 넘기면) 두 큐가 하나로 합쳐진다 — 이게 기존 동작.
    baseline = await align_by_embedding_dp(korean_cues, target_segments, provider)
    assert any(p.korean and p.korean.text == "안녕 하세요 반갑습니다" for p in baseline)

    # 화자가 다르면(원본 텍스트 기준) 합쳐지지 않고, 화자 표기 자체는 결과에
    # 안 나온다.
    korean_raw = [
        SegmentText(start=0.0, end=1.0, text="(순모) 안녕"),
        SegmentText(start=1.1, end=2.0, text="(투자자) 하세요 반갑습니다"),
    ]
    pairs = await align_by_embedding_dp(
        korean_cues, target_segments, provider, korean_raw_cues=korean_raw)
    assert not any(
        p.korean and "안녕" in p.korean.text and "반갑습니다" in p.korean.text for p in pairs)
    for p in pairs:
        if p.korean:
            assert "(순모)" not in p.korean.text and "(투자자)" not in p.korean.text


@pytest.mark.asyncio
async def test_align_by_embedding_dp_never_merges_all_caps_target_cue():
    """회귀(사용자 재현): 영화 제목 카드("TAL VEZ EL AMOR")처럼 전체
    대문자인 화면 텍스트 큐가, 시간이 가까운 옆 대사 큐와 하나로 합쳐지며
    통째로 사라지는 사고가 실제로 있었다 — 전체 대문자 큐는 절대 다른
    큐와 병합되면 안 된다."""
    korean_cues = [
        SegmentText(start=0.0, end=5.0, text="정말 슬픈 얘기야"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=2.0, text="TAL VEZ EL AMOR"),
        SegmentText(start=2.5, end=5.0, text="Es una historia muy triste."),
    ]
    provider = MockProvider()

    pairs = await align_by_embedding_dp(korean_cues, target_segments, provider)
    for p in pairs:
        if p.target and "TAL VEZ EL AMOR" in p.target.text:
            assert p.target.text == "TAL VEZ EL AMOR"


@pytest.mark.asyncio
async def test_align_by_embedding_dp_never_matches_all_caps_target_cue_to_korean():
    """회귀(사용자 재현): 병합만 막는 걸로는 부족했다 — 대문자 큐가 옆
    한국어 큐(노래 가사 등 실제로 무관한 내용)와 1:1로 매칭되면, AI가
    "번역이 틀렸다"며 화면 텍스트 자체를 엉뚱한 내용으로 덮어쓰는 사고가
    또 있었다(실제 사례: "TAL VEZ EL AMOR"가 "La luz del sol"로 교체됨).
    대문자 큐는 1:1 포함 어떤 매칭도 되면 안 되고, 한국어 원문 없이
    반쪽짜리로 남아야 한다."""
    korean_cues = [
        SegmentText(start=0.0, end=2.0, text="TAL VEZ EL AMOR와 시간이 겹치는 한국어"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=2.0, text="TAL VEZ EL AMOR"),
    ]
    provider = MockProvider()

    pairs = await align_by_embedding_dp(korean_cues, target_segments, provider)
    caption_pair = next(p for p in pairs if p.target and p.target.text == "TAL VEZ EL AMOR")
    assert caption_pair.korean is None


@pytest.mark.asyncio
async def test_align_by_embedding_dp_unmatched_target():
    korean_cues = [
        SegmentText(start=0.0, end=2.0, text="안녕하세요"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=2.0, text="Hola"),
        SegmentText(start=10.0, end=12.0, text="MADRID 1998"), # On-screen text
    ]
    provider = MockProvider()

    pairs = await align_by_embedding_dp(korean_cues, target_segments, provider)
    assert len(pairs) == 2
    assert pairs[0].korean is not None and pairs[0].target is not None
    assert pairs[1].korean is None
    assert pairs[1].target.text == "MADRID 1998"


@pytest.mark.asyncio
async def test_align_by_embedding_dp_long_sentence_3to1():
    # Long Korean sentence split into 3 cues matching 1 Spanish segment
    korean_cues = [
        SegmentText(start=0.0, end=1.5, text="안 돼"),
        SegmentText(start=1.6, end=3.5, text="지금은 작은 젓가락도"),
        SegmentText(start=3.6, end=5.5, text="자꾸만 놓치시는…"),
    ]
    target_segments = [
        SegmentText(start=0.0, end=5.5, text="No, ahora se le ca원 los palillos..."),
    ]
    provider = MockProvider()

    pairs = await align_by_embedding_dp(korean_cues, target_segments, provider)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "안 돼 지금은 작은 젓가락도 자꾸만 놓치시는…"
    assert pairs[0].target.text == "No, ahora se le ca원 los palillos..."

