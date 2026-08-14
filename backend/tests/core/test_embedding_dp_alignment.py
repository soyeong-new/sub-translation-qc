import pytest
from app.schemas import SegmentText
from app.providers.mock import MockProvider
from app.core.embedding_dp_alignment import align_by_embedding_dp, cosine_similarity, _compute_overlap_ratio


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2), 0.001) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v3), 0.001) == 0.0


def test_compute_overlap_ratio():
    # 0~2, 1~3 -> overlap 1 second, min length 2 seconds -> ratio 0.5
    assert pytest.approx(_compute_overlap_ratio(0.0, 2.0, 1.0, 3.0), 0.001) == 0.5
    # No overlap
    assert _compute_overlap_ratio(0.0, 1.0, 2.0, 3.0) == 0.0


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

