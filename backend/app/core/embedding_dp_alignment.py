"""OpenAI text-embedding-3-small과 DP(동적 계획법)를 이용해
한국어 SRT 큐와 대상언어(스페인어) SRT 세그먼트를 직접 정렬하는 모듈.

STT 없이 [한국어 SRT 큐]와 [스페인어 SRT 세그먼트]의 타임코드 겹침(Time Overlap)과
다국어 임베딩 유사도(Cosine Similarity)를 결합한 DP 알고리즘을 사용한다.
1:1 매칭뿐만 아니라 1:N, N:1 및 대역이 없는 독립 큐(화면 텍스트/미번역 줄)도
안전하게 분류해 반환한다.
"""

import math
from typing import List, Tuple, Optional
from app.schemas import SegmentText, AlignedPair
from app.providers.base import ModelProvider


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _compute_overlap_ratio(start1: float, end1: float, start2: float, end2: float) -> float:
    overlap_sec = max(0.0, min(end1, end2) - max(start1, start2))
    if overlap_sec <= 0:
        return 0.0
    len1 = max(0.001, end1 - start1)
    len2 = max(0.001, end2 - start2)
    return overlap_sec / min(len1, len2)


def _merge_cues(cues: List[SegmentText]) -> SegmentText:
    return SegmentText(
        start=cues[0].start,
        end=max(c.end for c in cues),
        text=" ".join(c.text for c in cues)
    )


MAX_MERGE_GAP_SECONDS = 3.0


async def align_by_embedding_dp(
    korean_cues: List[SegmentText],
    target_segments: List[SegmentText],
    provider: ModelProvider,
    skip_penalty: float = 0.6,
    sim_weight: float = 1.0,
    overlap_weight: float = 1.0,
) -> List[AlignedPair]:
    """한국어 SRT 큐와 대상언어 SRT 세그먼트를 임베딩+DP로 정렬한다.

    1. provider.get_embeddings()를 호출해 한국어와 스페인어 텍스트의 다국어 임베딩 벡터를 받는다.
    2. 시간 겹침 분률 + 임베딩 코사인 유사도로 종합 매칭 점수를 계산한다.
    3. DP 테이블(N+1 x M+1)을 채우며 최적 경로(1:1 ~ 4:1, 1:4, 스킵)를 구한다.
       - 긴 문장이 3~4개 큐로 나뉜 경우도 잘리지 않고 통째로 병합 정렬된다.
    4. 역추적하여 최종 AlignedPair 리스트를 생성한다.
    """
    if not korean_cues and not target_segments:
        return []
    if not korean_cues:
        return [AlignedPair(id=f"pair_{j+1}", korean=None, target=t) for j, t in enumerate(target_segments)]
    if not target_segments:
        return [AlignedPair(id=f"pair_korean_{i+1}", korean=k, target=None) for i, k in enumerate(korean_cues)]

    N = len(korean_cues)
    M = len(target_segments)

    # 1. Embeddings
    all_texts = [k.text for k in korean_cues] + [t.text for t in target_segments]
    embeddings = await provider.get_embeddings(all_texts)

    korean_embs = embeddings[:N]
    target_embs = embeddings[N:]

    # Helper function for matching score between a group of Korean cues and a group of Target segments
    def match_score(k_list: List[SegmentText], k_indices: List[int],
                    t_list: List[SegmentText], t_indices: List[int]) -> float:
        # Check gap between merged items
        if len(k_list) > 1:
            for idx in range(len(k_list) - 1):
                if k_list[idx + 1].start - k_list[idx].end > MAX_MERGE_GAP_SECONDS:
                    return -float('inf')
        if len(t_list) > 1:
            for idx in range(len(t_list) - 1):
                if t_list[idx + 1].start - t_list[idx].end > MAX_MERGE_GAP_SECONDS:
                    return -float('inf')

        # Calculate mean embedding for merged groups
        dim_size = len(korean_embs[0])
        k_emb = [sum(korean_embs[idx][dim] for idx in k_indices) / len(k_indices) for dim in range(dim_size)]
        t_emb = [sum(target_embs[idx][dim] for idx in t_indices) / len(t_indices) for dim in range(dim_size)]

        sim = cosine_similarity(k_emb, t_emb)
        overlap = _compute_overlap_ratio(k_list[0].start, k_list[-1].end, t_list[0].start, t_list[-1].end)

        # If no time overlap and low similarity (< 0.35), heavy penalty for bad match
        if overlap == 0.0 and sim < 0.35:
            return -float('inf')

        # Multi-cue merge bonus: encourages merging complete sentences rather than skipping tail cues
        bonus = 0.0
        if len(k_list) > 1 or len(t_list) > 1:
            bonus += 0.05 * (len(k_list) + len(t_list))

        return (sim_weight * sim) + (overlap_weight * overlap) + bonus

    # DP Table: dp[i][j] = max score matching first i korean cues and first j target segments
    dp = [[-float('inf')] * (M + 1) for _ in range(N + 1)]
    parent = {}  # (i, j) -> (prev_i, prev_j, action_type)

    dp[0][0] = 0.0

    for i in range(N + 1):
        for j in range(M + 1):
            curr_score = dp[i][j]
            if curr_score == -float('inf'):
                continue

            # Try dk (1..4) Korean cues and dt (1..4) Target segments
            for dk in range(1, 5):
                for dt in range(1, 5):
                    if dk > 1 and dt > 1 and (dk > 2 or dt > 2):
                        continue
                    if i + dk <= N and j + dt <= M:
                        k_sub = korean_cues[i : i + dk]
                        t_sub = target_segments[j : j + dt]
                        k_idxs = list(range(i, i + dk))
                        t_idxs = list(range(j, j + dt))

                        s = match_score(k_sub, k_idxs, t_sub, t_idxs)
                        if s != -float('inf'):
                            next_score = curr_score + s
                            if next_score > dp[i + dk][j + dt]:
                                dp[i + dk][j + dt] = next_score
                                parent[(i + dk, j + dt)] = (i, j, f"{dk}:{dt}")

            # Option: Skip Korean i (Korean Only)
            if i < N:
                s = -skip_penalty
                if curr_score + s > dp[i + 1][j]:
                    dp[i + 1][j] = curr_score + s
                    parent[(i + 1, j)] = (i, j, "SKIP_KOREAN")

            # Option: Skip Target j (Target Only / On-screen text)
            if j < M:
                s = -skip_penalty
                if curr_score + s > dp[i][j + 1]:
                    dp[i][j + 1] = curr_score + s
                    parent[(i, j + 1)] = (i, j, "SKIP_TARGET")

    # Backtrack
    curr_i, curr_j = N, M
    actions = []
    while (curr_i, curr_j) in parent:
        prev_i, prev_j, act = parent[(curr_i, curr_j)]
        actions.append((prev_i, prev_j, curr_i, curr_j, act))
        curr_i, curr_j = prev_i, prev_j

    actions.reverse()

    pairs: List[AlignedPair] = []
    pair_count = 1

    for prev_i, prev_j, curr_i, curr_j, act in actions:
        if act == "SKIP_TARGET":
            pairs.append(AlignedPair(
                id=f"pair_{pair_count}",
                korean=None,
                target=target_segments[prev_j]
            ))
            pair_count += 1
        elif act == "SKIP_KOREAN":
            pairs.append(AlignedPair(
                id=f"pair_korean_{pair_count}",
                korean=korean_cues[prev_i],
                target=None
            ))
            pair_count += 1
        else:
            pairs.append(AlignedPair(
                id=f"pair_{pair_count}",
                korean=_merge_cues(korean_cues[prev_i:curr_i]) if curr_i > prev_i else None,
                target=_merge_cues(target_segments[prev_j:curr_j]) if curr_j > prev_j else None
            ))
            pair_count += 1

    return pairs

