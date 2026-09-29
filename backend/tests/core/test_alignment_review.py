import pytest

from app.schemas import AlignedPair, SegmentText
from app.core.alignment_review import review_ambiguous_alignment
from app.providers.live import LiveModelProvider


def cue(start, end, text):
    return SegmentText(start=start, end=end, text=text)


class Reviewer:
    def __init__(self, groups):
        self.groups = groups
        self.calls = []

    async def realign_subtitles(self, korean, target, baseline):
        self.calls.append((korean, target, baseline))
        return self.groups


@pytest.mark.asyncio
async def test_review_repairs_french_shift_without_changing_text_or_time():
    korean = [cue(0, 2, "우리가 뭐 어때서?"), cue(2, 4, "착하고 성실하대"),
              cue(4, 6, "그래서 놓치기 싫대")]
    target = [cue(0, 1, "Qu'est-ce qu'il ne va pas avec nous ?"),
              cue(1, 2, "Il est gentil et sérieux."),
              cue(2, 4, "Elle ne veut pas le perdre.")]
    pairs = [AlignedPair(id="pair_1", korean=korean[0],
                         target=cue(0, 2, "Qu'est-ce qu'il ne va pas avec nous ? Il est gentil et sérieux.")),
             AlignedPair(id="pair_2", korean=korean[1], target=target[2]),
             AlignedPair(id="pair_3", korean=korean[2], target=None)]
    reviewer = Reviewer([{"k_ids":[0],"t_ids":[0]}, {"k_ids":[1],"t_ids":[1]},
                         {"k_ids":[2],"t_ids":[2]}])

    actual = await review_ambiguous_alignment(korean, target, pairs, reviewer)

    assert [(p.korean.text, p.target.text) for p in actual] == list(zip(
        [c.text for c in korean], [c.text for c in target]))
    assert [p.target.start for p in actual] == [c.start for c in target]
    assert len(reviewer.calls) == 1


@pytest.mark.asyncio
async def test_review_rejects_missing_cue_and_keeps_baseline():
    korean = [cue(0, 1, "질문"), cue(1, 2, "답")]
    target = [cue(0, 1, "Question?"), cue(1, 2, "Answer.")]
    pairs = [AlignedPair(id="pair_1", korean=korean[0],
                         target=cue(0, 2, "Question? Answer.")),
             AlignedPair(id="pair_2", korean=korean[1], target=None)]
    reviewer = Reviewer([{"k_ids":[0],"t_ids":[0]}])
    warnings = []

    actual = await review_ambiguous_alignment(korean, target, pairs, reviewer, warnings=warnings)

    assert actual == pairs
    assert warnings and warnings[0]["stage"] == "자막 정렬 재검토"


@pytest.mark.asyncio
async def test_review_keeps_baseline_when_gpt_request_fails():
    korean = [cue(0, 2, "질문")]
    target = [cue(0, 1, "Question?"), cue(1, 2, "Answer.")]
    pairs = [AlignedPair(id="pair_1", korean=korean[0],
                         target=cue(0, 2, "Question? Answer."))]

    class FailedReviewer:
        async def realign_subtitles(self, korean, target, baseline):
            raise ConnectionError("GPT unavailable")

    warnings = []
    assert await review_ambiguous_alignment(
        korean, target, pairs, FailedReviewer(), warnings=warnings) == pairs
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_review_skips_unmerged_alignment():
    korean = [cue(0, 1, "질문")]
    target = [cue(0, 1, "Question?")]
    pairs = [AlignedPair(id="pair_1", korean=korean[0], target=target[0])]
    reviewer = Reviewer([])

    assert await review_ambiguous_alignment(korean, target, pairs, reviewer) == pairs
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_live_provider_dispatches_alignment_review_to_gpt():
    provider = object.__new__(LiveModelProvider)
    provider._gpt = Reviewer([{"k_ids": [0], "t_ids": [0]}])

    result = await provider.realign_subtitles([{"id": 0}], [{"id": 0}], [])

    assert result == [{"k_ids": [0], "t_ids": [0]}]
    assert provider._gpt.calls == [([{"id": 0}], [{"id": 0}], [])]


@pytest.mark.asyncio
async def test_dense_merges_do_not_create_an_unbounded_gpt_request():
    korean = [cue(i, i+1, f"대사{i}") for i in range(30)]
    target = [cue(i/2, (i+1)/2, f"Line {i}.") for i in range(60)]
    pairs = [AlignedPair(id=f"pair_{i+1}", korean=korean[i],
                         target=cue(i, i+1, f"Line {2*i}. Line {2*i+1}."))
             for i in range(30)]

    class EchoReviewer:
        def __init__(self):
            self.sizes = []

        async def realign_subtitles(self, ko, translated, baseline):
            self.sizes.append(len(ko) + len(translated))
            return baseline

    reviewer = EchoReviewer()
    await review_ambiguous_alignment(korean, target, pairs, reviewer)
    assert reviewer.sizes
    assert max(reviewer.sizes) <= 36  # at most 12 paired groups of up to 3 cues
