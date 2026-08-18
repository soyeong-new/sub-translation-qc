"""OpenAI text-embedding-3-small과 DP(동적 계획법)를 이용해
한국어 SRT 큐와 대상언어(스페인어) SRT 세그먼트를 직접 정렬하는 모듈.

STT 없이 [한국어 SRT 큐]와 [스페인어 SRT 세그먼트]의 타임코드 겹침(Time Overlap)과
다국어 임베딩 유사도(Cosine Similarity)를 결합한 DP 알고리즘을 사용한다.
1:1 매칭뿐만 아니라 1:N, N:1 및 대역이 없는 독립 큐(화면 텍스트/미번역 줄)도
안전하게 분류해 반환한다.
"""

import math
import re
from typing import List, Tuple, Optional
from app.schemas import SegmentText, AlignedPair
from app.providers.base import ModelProvider


def _clean_text_for_embedding(text: str) -> str:
    """한국어 자막 텍스트에서 지문, 효과음, 화자 표기를 제거하고 순수 대사만 남긴다."""
    if not text:
        return ""
    # 1. 대괄호/소괄호/중괄호/화살괄호 지문 및 효과음 제거 (줄바꿈 포함)
    cleaned = re.sub(r'\[[\s\S]*?\]|\([\s\S]*?\)|\{[\s\S]*?\}|<[\s\S]*?>', '', text)
    # 2. 줄 단위 화자 표기 (예: "화자 1:", "(순모)", "김우택:", "순모:") 제거
    lines = []
    for line in cleaned.split('\n'):
        line = re.sub(r'^[가-힣A-Za-z0-9_\s]{1,12}\s*[:：]', '', line)
        lines.append(line.strip())
    cleaned_str = "\n".join(l for l in lines if l)
    return cleaned_str.strip()


def _ends_with_terminal_punctuation(text: str) -> bool:
    """마침표(.), 물음표(?), 느낌표(!)로 끝난 완성된 문장인지 확인한다."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("...") or stripped.endswith("…"):
        return False
    return stripped[-1] in (".", "?", "!")


def _has_speaker_dash(text: str) -> bool:
    """줄 첫머리에 대시(-)가 붙은 2인 화자 대사인지 확인한다."""
    stripped = text.strip()
    return stripped.startswith("-") or "\n-" in text


_LETTERS_RE = re.compile(r'[a-zA-ZáéíóúñüÁÉÍÓÚÑÜ]')


def _is_all_caps_text(text: str) -> bool:
    """자막 관례상 화면 텍스트(영화 제목 카드, 배너, 신문 헤드라인 등 —
    대사가 아닌 것)는 전부 대문자로 적는 경우가 많다(실제 데이터로 확인:
    "TAL VEZ EL AMOR" 같은 제목 카드). 대사는 이 패턴에 거의 안 걸린다.
    글자 3개 미만이면(짧은 감탄사 등 오탐 위험) 판단하지 않는다."""
    letters = _LETTERS_RE.findall(text)
    if len(letters) < 3:
        return False
    return all(ch == ch.upper() for ch in letters)


_LEADING_TAG_RE = re.compile(r'^\(([^)]{1,10})\)\s*')


def _leading_speaker_tag(raw_text: str) -> Optional[str]:
    """정제 전 원본 큐 텍스트 맨 앞의 "(화자명)" 표기를 뽑아낸다 — 매칭 판단에만
    쓰고 화면/SRT에는 절대 노출하지 않는다(그건 _clean_text_for_embedding이
    이미 지운다). 효과음("(한숨)")인지 화자명("(순모)")인지는 구분하지
    않는다 — 둘 다 "괄호 태그"로 취급해, 인접한 두 큐의 태그가 서로 다르면
    (둘 다 있을 때만) 한 화자의 연속 발화가 아니라고 보고 병합을 막는다."""
    if not raw_text:
        return None
    m = _LEADING_TAG_RE.match(raw_text.strip())
    return m.group(1).strip() if m else None


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _compute_overlap_ratio(start1: float, end1: float, start2: float, end2: float) -> float:
    overlap_sec = max(0.0, min(end1, end2) - max(start1, start2))
    if overlap_sec > 0:
        # min(len1, len2)로 나누면(포함비) 짧은 큐가 훨씬 긴 큐 안에 완전히
        # 포함되기만 해도 무조건 1.0(만점)이 나온다 — 긴 쪽 내용의 일부만
        # 커버해도 만점이라, 길이 차가 큰 큐끼리 만나면 DP가 "짧은 쪽 하나만
        # 매칭"을 정답인 다중 병합보다 그리디하게 선호하는 편향이 생긴다
        # (실제로 KR 두 줄이 합쳐진 ES 한 줄을, KR 첫 줄 하나만으로 통째로
        # 채가는 오정렬을 만들었다). IoU(합집합 대비 교집합)로 바꾸면 짧은
        # 쪽이 긴 쪽을 다 못 덮을수록 점수가 깎여 이 편향이 없어진다.
        union_sec = max(end1, end2) - min(start1, start2)
        return overlap_sec / union_sec

    # 직접 겹치지 않아도 간격(gap)이 3초 이내면 부드러운 근접 점수 부여 (타임코드 오차 보정)
    gap = max(0.0, max(start1, start2) - min(end1, end2))
    if gap < 3.0:
        return max(0.0, 0.3 * (1.0 - gap / 3.0))
    return 0.0


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
    korean_raw_cues: Optional[List[SegmentText]] = None,
) -> List[AlignedPair]:
    """한국어 SRT 큐와 대상언어 SRT 세그먼트를 임베딩+DP로 정렬한다.

    korean_raw_cues: korean_cues와 1:1 대응하는, 화자 표기(예: "(순모)")를
    지우기 전 원본 텍스트. 병합 가능 여부 판단(화자 바뀜 감지)에만 쓰고
    임베딩·출력 텍스트에는 절대 반영하지 않는다 — 화자 표기가 화면/SRT에
    노출되면 안 되기 때문이다. 안 넘기면(기존 호출부·테스트) 이 판단을
    건너뛴다."""
    if not korean_cues and not target_segments:
        return []
    if not korean_cues:
        return [AlignedPair(id=f"pair_{j+1}", korean=None, target=t) for j, t in enumerate(target_segments)]
    if not target_segments:
        return [AlignedPair(id=f"pair_korean_{i+1}", korean=k, target=None) for i, k in enumerate(korean_cues)]

    N = len(korean_cues)
    M = len(target_segments)
    korean_raw_texts = [c.text for c in korean_raw_cues] if korean_raw_cues else None

    # 1. Embeddings (지문/효과음/화자 제거 후 텍스트로 임베딩 벡터 추출)
    korean_texts_cleaned = [_clean_text_for_embedding(k.text) or k.text for k in korean_cues]
    target_texts_cleaned = [_clean_text_for_embedding(t.text) or t.text for t in target_segments]

    all_texts = korean_texts_cleaned + target_texts_cleaned
    embeddings = await provider.get_embeddings(all_texts)

    korean_embs = embeddings[:N]
    target_embs = embeddings[N:]

    # Helper function for matching score between a group of Korean cues and a group of Target segments
    def match_score(k_list: List[SegmentText], k_indices: List[int],
                    t_list: List[SegmentText], t_indices: List[int]) -> float:
        # 0. 전체 대문자 큐(화면 텍스트/제목 카드 — 대사 아님)는 어떤 한국어
        # 큐와도 절대 매칭하지 않는다(1:1 포함) — 병합만 막았을 때도 실제로
        # 무관한 한국어 줄(예: 노래 가사)과 1:1로 잘못 짝지어져, AI가
        # "번역이 틀렸다"며 화면 텍스트 자체를 엉뚱한 내용으로 덮어쓰는
        # 사고가 있었다. 한국어 원문과 비교할 근거가 없으므로 AI 검증도
        # 건너뛰고(대상언어만 있는 반쪽 Segment) 원본 그대로 내보낸다.
        if any(_is_all_caps_text(t.text) for t in t_list):
            return -float('inf')

        # 1. Check gap, speaker dashes, and sentence terminal punctuation between merged items
        if len(k_list) > 1:
            for idx in range(len(k_list) - 1):
                if k_list[idx + 1].start - k_list[idx].end > MAX_MERGE_GAP_SECONDS:
                    return -float('inf')
                # 2인 대시 대사(-)가 있으면 다중 큐 병합 금지
                if _has_speaker_dash(k_list[idx].text) or _has_speaker_dash(k_list[idx + 1].text):
                    return -float('inf')
                # 종결 기호(., ?, !)로 끝난 완성된 한국어 문장이면 다음 문장과 병합 차단
                if _ends_with_terminal_punctuation(k_list[idx].text):
                    return -float('inf')
                # 인접한 두 큐에 화자 표기가 둘 다 있고 서로 다르면(화자가
                # 바뀌었다는 뜻) 한 발화로 병합하지 않는다. 표기가 없는
                # 큐는(같은 화자가 이어 말해 표기를 생략한 경우가 흔함)
                # 판단 근거가 없으므로 막지 않는다.
                if korean_raw_texts is not None:
                    tag_a = _leading_speaker_tag(korean_raw_texts[k_indices[idx]])
                    tag_b = _leading_speaker_tag(korean_raw_texts[k_indices[idx + 1]])
                    if tag_a and tag_b and tag_a != tag_b:
                        return -float('inf')

        if len(t_list) > 1:
            for idx in range(len(t_list) - 1):
                if t_list[idx + 1].start - t_list[idx].end > MAX_MERGE_GAP_SECONDS:
                    return -float('inf')
                if _has_speaker_dash(t_list[idx].text) or _has_speaker_dash(t_list[idx + 1].text):
                    return -float('inf')


        # Calculate mean embedding for merged groups
        dim_size = len(korean_embs[0])
        k_emb = [sum(korean_embs[idx][dim] for idx in k_indices) / len(k_indices) for dim in range(dim_size)]
        t_emb = [sum(target_embs[idx][dim] for idx in t_indices) / len(t_indices) for dim in range(dim_size)]

        sim = cosine_similarity(k_emb, t_emb)
        overlap = _compute_overlap_ratio(k_list[0].start, k_list[-1].end, t_list[0].start, t_list[-1].end)

        # Allow matching if there is time overlap or reasonably close proximity with similarity
        if overlap == 0.0 and sim < 0.25:
            return -float('inf')

        # 억지 다중 큐 병합 보너스 제거 -> 1:1 매칭 기본 가중치 유지
        return (sim_weight * sim) + (overlap_weight * overlap)



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

