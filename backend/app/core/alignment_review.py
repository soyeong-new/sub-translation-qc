"""Context review for subtitle groups joined by the initial DP alignment."""

from app.schemas import AlignedPair, SegmentText
from app.core.embedding_dp_alignment import _is_all_caps_text


def _group_ids(merged: SegmentText | None, cues: list[SegmentText], cursor: int) -> list[int]:
    if merged is None:
        return []
    for end in range(cursor + 1, min(cursor + 4, len(cues)) + 1):
        group = cues[cursor:end]
        if (merged.start == group[0].start
                and merged.end == max(c.end for c in group)
                and merged.text == " ".join(c.text for c in group)):
            return list(range(cursor, end))
    raise ValueError("Cannot recover original subtitle cues from DP group")


def _groups_from_pairs(korean, target, pairs):
    groups = []
    ki = ti = 0
    for pair in pairs:
        k_ids = _group_ids(pair.korean, korean, ki)
        t_ids = _group_ids(pair.target, target, ti)
        groups.append({"k_ids": k_ids, "t_ids": t_ids})
        ki += len(k_ids)
        ti += len(t_ids)
    if ki != len(korean) or ti != len(target):
        raise ValueError("DP groups do not cover all subtitle cues")
    return groups


def _merge(cues: list[SegmentText], ids: list[int]) -> SegmentText | None:
    if not ids:
        return None
    group = [cues[i] for i in ids]
    return SegmentText(start=group[0].start, end=max(c.end for c in group),
                       text=" ".join(c.text for c in group))


def _validate(groups, expected_k, expected_t, target):
    if not isinstance(groups, list) or not groups:
        raise ValueError("Missing alignment groups")
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Malformed alignment group")
        ks, ts = group.get("k_ids"), group.get("t_ids")
        if not isinstance(ks, list) or not isinstance(ts, list) or (not ks and not ts):
            raise ValueError("Malformed cue IDs")
        if any(type(i) is not int for i in ks + ts):
            raise ValueError("Cue IDs must be integers")
        if any(_is_all_caps_text(target[i].text) for i in ts) and ks:
            raise ValueError("Screen title must remain unpaired")
    if [i for g in groups for i in g["k_ids"]] != expected_k:
        raise ValueError("Korean cue was missing, duplicated or reordered")
    if [i for g in groups for i in g["t_ids"]] != expected_t:
        raise ValueError("Target cue was missing, duplicated or reordered")


async def review_ambiguous_alignment(
    korean_cues: list[SegmentText], target_cues: list[SegmentText],
    pairs: list[AlignedPair], provider, *, warnings: list | None = None,
) -> list[AlignedPair]:
    """Review only windows containing a merged translated cue; keep invalid results unchanged."""
    if not pairs:
        return pairs
    try:
        baseline = _groups_from_pairs(korean_cues, target_cues, pairs)
    except ValueError as exc:
        if warnings is not None:
            warnings.append({"stage": "자막 정렬 재검토", "message": str(exc)})
        return pairs
    flagged = [i for i, g in enumerate(baseline) if len(g["t_ids"]) > 1]
    if not flagged:
        return pairs

    windows = []
    covered_until = 0
    for index in flagged:
        if index < covered_until:
            continue
        start = max(covered_until, index - 4)
        end = min(len(pairs), index + 5, start + 12)
        windows.append((start, end))
        covered_until = end

    revised = pairs[:]
    for start, end in reversed(windows):
        original = baseline[start:end]
        k_ids = [i for g in original for i in g["k_ids"]]
        t_ids = [i for g in original for i in g["t_ids"]]
        korean = [{"id": i, "start": korean_cues[i].start,
                   "end": korean_cues[i].end, "text": korean_cues[i].text} for i in k_ids]
        target = [{"id": i, "start": target_cues[i].start,
                   "end": target_cues[i].end, "text": target_cues[i].text} for i in t_ids]
        try:
            groups = await provider.realign_subtitles(korean, target, original)
            _validate(groups, k_ids, t_ids, target_cues)
            replacement = [AlignedPair(id="", korean=_merge(korean_cues, g["k_ids"]),
                                       target=_merge(target_cues, g["t_ids"])) for g in groups]
        except Exception as exc:
            if warnings is not None:
                warnings.append({"stage": "자막 정렬 재검토", "message": str(exc)})
            continue
        revised[start:end] = replacement
    return [AlignedPair(id=f"pair_{i+1}", korean=p.korean, target=p.target)
            for i, p in enumerate(revised)]
