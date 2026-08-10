"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from app.providers.base import ModelProvider
from app.core.ingest import load_srt, extract_audio, generate_video_proxy, split_audio_into_chunks
from app.core.pronoun_hints import find_pronoun_hint
from app.core.alignment import align
from app.core.format_rules import check_line_length, check_ellipsis, MAX_LINE_CHARS, MAX_LINES
from app.core.safety_net import shrink_violating_lines
from app.language_profiles.loader import load_profile
from app.knowledge.loader import (
    load_knowledge, load_sensitive_terms, load_glossary, load_cta_patterns,
    load_profanity_dictionary,
)
from app.core.pretreatment import run_pretreatment
from app.core.grammar_necessity import check_grammar_necessity
from app.schemas import SegmentText, Finding

logger = logging.getLogger(__name__)

# gpt-4o-mini-transcribe(OpenAI 오디오 API)는 25MB/약 1500초(25분) 제한이
# 있다. 16kHz mono 16bit PCM WAV는 초당 32KB라 25MB는 약 781초(≈13분)에
# 해당하므로, 이보다 여유 있게 낮춰서 잡는다. split_audio_into_chunks의
# 기본값과 같은 값이지만, 병합 시 오프셋 계산에 이 상수를 그대로 다시
# 써야 하므로 여기서도 명시적으로 갖고 있는다.
STT_CHUNK_SECONDS = 600.0


def _offset_segments(segments: list, offset_seconds: float) -> list:
    """STT 조각 결과의 타임코드(그 조각 파일 안에서 0초부터 시작하는 상대
    시각)를 에피소드 전체 기준 절대 시각으로 보정한다."""
    return [
        {**s, "start": s["start"] + offset_seconds, "end": s["end"] + offset_seconds}
        for s in segments
    ]


async def _transcribe_in_chunks(provider: ModelProvider, wav_path: str) -> list:
    """긴 오디오를 여러 조각으로 나눠 각각 STT한 뒤 이어붙인다.
    asyncio.gather는 완료 순서와 무관하게 항상 입력 순서대로 결과를
    반환하므로(공식 보장 사항), 조각을 시간 순서대로 넘기기만 하면 병렬로
    돌려도 최종 결과 순서가 흐트러지지 않는다. split_audio_into_chunks가
    분할 없이 원본 경로를 그대로 반환했을 수 있으므로(짧은 오디오), 조각
    정리(unlink) 시 원본 wav_path는 그 경로로는 절대 지우지 않는다 — 분할이
    안 일어난 경우 원본 정리는 호출자의 finally 블록이 전담한다.

    반면 분할이 실제로 일어난 경우(chunk_paths가 원본과 다른 실제 조각
    파일들)에는, 조각들이 원본을 바이트 단위로 완전히 대체하는 복사본이라
    원본이 그 순간부터 죽은 데이터가 된다 — 병렬 transcribe가 도는 동안
    원본(~230MB급)까지 같이 들고 있으면 오디오 관련 디스크 사용량이 이
    기능이 지원하려는 바로 그 상황(긴 콘텐츠)에서 불필요하게 두 배가 된다.
    그래서 이 경우엔 transcribe를 시작하기 전에 원본을 바로 지운다."""
    chunk_paths = await asyncio.to_thread(
        split_audio_into_chunks, wav_path, STT_CHUNK_SECONDS)
    if chunk_paths != [wav_path]:
        Path(wav_path).unlink(missing_ok=True)
    try:
        chunk_results = await asyncio.gather(
            *(provider.transcribe(p) for p in chunk_paths)
        )
    finally:
        for p in chunk_paths:
            if p != wav_path:
                Path(p).unlink(missing_ok=True)
    merged: list = []
    for i, segments in enumerate(chunk_results):
        merged.extend(_offset_segments(segments, i * STT_CHUNK_SECONDS))
    if not merged:
        # 개별 조각에 세그먼트가 없는 것(무음 구간)은 정상이지만, 모든
        # 조각을 병합한 결과가 통째로 비어 있다면 에피소드 전체에 대사가
        # 없다는 뜻이다 — 이 경우는 여전히 진짜 실패다(과거 조각 분할 이전,
        # transcribe가 에피소드 전체를 한 번에 처리하던 시절과 동일한 판단
        # 기준을 병합 레벨로 옮긴 것).
        raise ValueError("GPT STT 응답에 세그먼트가 없음")
    return merged


async def _run_grammar_necessity_check(
    pairs: list, profile: dict, english_segments: list,
    target_version_id: str,
) -> tuple[list, list]:
    """문법 필요성 판단(줄 단위, spaCy 형태소 분석): 성별/격식 판단이 실제로
    필요한 줄만 골라낸다. 값 자체는 한국어 원문(어미/호칭)으로 먼저 자동
    판정을 시도하고 — 안 되면 영어 SRT 대명사 힌트로, 그래도 안 되면 사람에게
    묻는다(design §정말 판단하기 어려운 것만 질문). 반환값은
    (segment_resolutions, warnings)."""
    grammar_pairs = [
        {"id": p.id, "target_text": p.target.text if p.target else "",
         "korean_text": p.korean.text if p.korean else ""}
        for p in pairs if p.target is not None
    ]
    segment_resolutions: list = []
    warnings: list = []
    try:
        # spaCy 형태소 분석은 CPU 바운드 동기 작업이라, asyncio.to_thread로
        # 감싸지 않으면 이 코루틴이 이벤트 루프를 막는다 — extract_audio/
        # generate_video_proxy와 동일한 이유.
        grammar_flags = await asyncio.to_thread(
            check_grammar_necessity, grammar_pairs, profile)
        flags_by_id = {f["id"]: f for f in grammar_flags}
        flagged_pairs = [
            p for p in pairs
            if flags_by_id.get(p.id, {}).get("gender_check_needed")
            or flags_by_id.get(p.id, {}).get("formality_check_needed")
        ]
        for p in flagged_pairs:
            flags = flags_by_id[p.id]
            gender_needed = bool(flags.get("gender_check_needed"))
            resolved_gender = flags.get("resolved_gender_from_korean")
            # 인칭/영어 힌트를 하나로 묶어 기존 english_pronoun_hint 컬럼(JSON) 그대로
            # 재사용한다 — 새 DB 컬럼 없이 "이건 몇 인칭 얘기인지" 힌트까지 얹는다.
            # 아무 정보도 없으면(인칭도 못 뽑고 영어 힌트도 없으면) None 그대로 둔다 —
            # 빈 껍데기 dict를 굳이 보여줄 이유가 없다.
            grammatical_person = flags.get("grammatical_person")
            gender_hint = {"grammatical_person": grammatical_person} if grammatical_person else None
            # 한국어 호칭/대명사로 못 정했을 때만 영어 SRT 폴백을 계산한다 —
            # 한국어로 이미 확정됐으면 계산할 이유가 없다.
            if gender_needed and resolved_gender is None and english_segments and p.target is not None:
                english_hint = find_pronoun_hint(p.target.start, p.target.end, english_segments)
                if english_hint:
                    gender_hint = {**(gender_hint or {}), **english_hint}
                    if english_hint["he_count"] > 0 and english_hint["she_count"] == 0:
                        resolved_gender = "male"
                    elif english_hint["she_count"] > 0 and english_hint["he_count"] == 0:
                        resolved_gender = "female"
            segment_resolutions.append({
                "segment_id": p.id,
                "gender_check_needed": gender_needed,
                "formality_check_needed": bool(flags.get("formality_check_needed")),
                "resolved_gender": resolved_gender,
                "resolved_formality": flags.get("resolved_formality"),
                "english_pronoun_hint": gender_hint,
            })
    except Exception as exc:
        logger.exception(
            "문법 필요성 판단 실패, 성별/격식 체크 생략하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        warnings.append({"stage": "문법 필요성 판단", "message": str(exc)})
    return segment_resolutions, warnings


def _dedupe_by_segment_id(corrections: list) -> dict:
    """동일 segment_id에 대해 correction이 두 개 이상 오면 첫 번째만 채택한다
    (LLM이 프롬프트 계약을 어기고 같은 세그먼트를 중복 반환할 가능성에 대비)."""
    seen: dict = {}
    for c in corrections:
        seen.setdefault(c["segment_id"], c)
    return seen


def _reconcile_dual_verification(
    claude_corrections: list, gpt_corrections: list,
) -> tuple[list, list, list]:
    """Claude/GPT가 각각 독립적으로 낸 교정 제안을 segment_id 기준으로 비교한다.
    같은 줄을 둘 다 지적한 경우는 아직 "합의 후보"일 뿐이다 — 문구가 다를 수
    있어(두 LLM이 토씨까지 똑같이 쓸 확률은 낮음), segment_id만으로 합의를
    확정하면 안 된다. 실제 합의 여부는 _check_equivalence가 두 문구의 의미가
    같은지 교차 확인한 뒤에 정해진다. 반환값은
    (candidate_pairs, claude_only, gpt_only) — candidate_pairs는
    (claude_correction, gpt_correction) 튜플 리스트."""
    claude_by_id = _dedupe_by_segment_id(claude_corrections)
    gpt_by_id = _dedupe_by_segment_id(gpt_corrections)
    candidate_ids = claude_by_id.keys() & gpt_by_id.keys()
    claude_only_ids = claude_by_id.keys() - gpt_by_id.keys()
    gpt_only_ids = gpt_by_id.keys() - claude_by_id.keys()

    candidate_pairs = [(claude_by_id[sid], gpt_by_id[sid]) for sid in candidate_ids]
    claude_only = [claude_by_id[sid] for sid in claude_only_ids]
    gpt_only = [gpt_by_id[sid] for sid in gpt_only_ids]
    return candidate_pairs, claude_only, gpt_only


async def _empty_list() -> list:
    return []


async def _safe_call(coro, label: str, note: str, target_version_id: str, warnings: list) -> list:
    """실패해도 파이프라인 전체를 죽이지 않고 빈 결과로 대체한다 — 호출부가
    warnings 리스트에 사유를 남겨 검수자에게 알린다."""
    try:
        return await coro
    except Exception as exc:
        logger.exception(
            "%s 실패, %s (target_version_id=%s)", label, note, target_version_id)
        warnings.append({"stage": label, "message": str(exc)})
        return []


async def _check_equivalence(
    candidate_pairs: list, korean_text_by_id: dict,
    provider: ModelProvider, profile: dict, target_version_id: str,
) -> tuple[list, list, list, list]:
    """합의 후보(같은 줄을 둘 다 지적했지만 문구는 다를 수 있음)가 실제로 같은
    뜻인지 Claude/GPT 양쪽에 교차로 물어 확인한다 — 병합 판정을 한쪽 모델에만
    맡기면 "합의"라는 신뢰 신호 자체가 다시 단일 모델 신뢰 문제로 돌아가므로,
    둘 다 "같다"고 해야만 진짜 합의로 확정한다(호출 실패도 안전하게 불일치로
    처리). 반환값은 (true_agreed, disputed_claude, disputed_gpt, warnings)."""
    warnings: list = []
    if not candidate_pairs:
        return [], [], [], warnings

    items = [
        {"id": c["segment_id"], "korean_text": korean_text_by_id.get(c["segment_id"], ""),
         "text_a": c["corrected_text"], "text_b": g["corrected_text"]}
        for c, g in candidate_pairs
    ]

    claude_verdicts, gpt_verdicts = await asyncio.gather(
        _safe_call(provider.check_equivalence_with_claude(items, profile),
                   "합의 동등성 확인(Claude)", "해당 후보들은 불일치로 안전하게 처리",
                   target_version_id, warnings),
        _safe_call(provider.check_equivalence_with_gpt(items, profile),
                   "합의 동등성 확인(GPT)", "해당 후보들은 불일치로 안전하게 처리",
                   target_version_id, warnings),
    )
    claude_verdict_by_id = {v["id"]: v["equivalent"] for v in claude_verdicts}
    gpt_verdict_by_id = {v["id"]: v["equivalent"] for v in gpt_verdicts}

    true_agreed: list = []
    disputed_claude: list = []
    disputed_gpt: list = []
    for claude_correction, gpt_correction in candidate_pairs:
        sid = claude_correction["segment_id"]
        both_confirm = (
            claude_verdict_by_id.get(sid, False) and gpt_verdict_by_id.get(sid, False)
        )
        if both_confirm:
            true_agreed.append(gpt_correction)
        else:
            disputed_claude.append(claude_correction)
            disputed_gpt.append(gpt_correction)
    return true_agreed, disputed_claude, disputed_gpt, warnings


async def _back_translate_proposals(
    provider: ModelProvider, profile: dict,
    agreed: list, claude_only: list, gpt_only: list, target_version_id: str,
) -> tuple[dict, list]:
    """제안된 문구를 반대쪽 모델이 한국어로 역번역한다(감사/참고용) — 자기가
    쓴 문구를 자기가 역번역하면 스스로의 오류를 매끄럽게 얼버무려 가릴 위험이
    있어(같은 모델의 왕복 번역은 오류를 숨기는 경향) 항상 교차 검증한다.
    claude_only는 GPT가, (agreed + gpt_only는 전부 GPT 문구이므로) Claude가
    역번역한다. 반환값은 (segment_id -> 한국어 역번역 텍스트, warnings)."""
    warnings: list = []
    claude_authored_texts = [
        {"id": c["segment_id"], "text": c["corrected_text"]} for c in claude_only
    ]
    gpt_authored_texts = [
        {"id": c["segment_id"], "text": c["corrected_text"]} for c in agreed + gpt_only
    ]

    claude_authored_backtranslated, gpt_authored_backtranslated = await asyncio.gather(
        _safe_call(provider.back_translate_with_gpt(claude_authored_texts, profile),
                   "Claude 제안 역번역", "역번역 없이 계속 진행", target_version_id, warnings)
        if claude_authored_texts else _empty_list(),
        _safe_call(provider.back_translate_with_claude(gpt_authored_texts, profile),
                   "GPT 제안 역번역", "역번역 없이 계속 진행", target_version_id, warnings)
        if gpt_authored_texts else _empty_list(),
    )
    backtranslation_by_id = {
        r["id"]: r["korean_text"]
        for r in claude_authored_backtranslated + gpt_authored_backtranslated
    }
    return backtranslation_by_id, warnings


def _make_dual_verification_finding(
    target_version_id: str, pair, correction: dict,
    status: str, model_label: str, backtranslation_by_id: dict,
) -> Finding:
    original_text = pair.target.text
    corrected_text = correction["corrected_text"]
    description = correction["description"]
    backtranslation = backtranslation_by_id.get(correction["segment_id"])
    if backtranslation:
        description = f"{description} (한국어 역번역 참고: {backtranslation})"
    return Finding(
        id=f"finding_{correction['segment_id']}_{model_label}_{correction['category']}",
        target_version_id=target_version_id, segment_id=correction["segment_id"],
        category=correction["category"], description=description,
        original_text=original_text, suggested_text=corrected_text,
        confidence=1.0, source="llm", model=model_label,
        status=status, final_text=corrected_text if status == "approved" else "",
    )


# 영화 전체 pair를 한 콜에 몰아넣으면 Claude/GPT 응답이 토큰 한도에서 잘려
# JSON 파싱이 통째로 실패하고(그 콜에 담긴 구간 전체가 무효 처리됨), 항목이
# 많을수록 모델이 segment_id를 엉뚱한 줄에 붙이는 오귀속도 늘어난다. 1차
# 방어선은 _split_into_scenes의 LLM 씬 분할(문맥 경계에서 자름)이고, 이
# 함수는 그게 실패했을 때의 전체 폴백이자 씬이 너무 클 때의 2차 안전장치다
# — 대사 사이 타임코드 공백(장면 전환 등 자연스러운 끊김)에서만 자르고,
# 공백 없이 대화가 계속 이어지면 최대 크기에서 강제로 자른다.
CHUNK_MIN_SIZE = 20
CHUNK_MAX_SIZE = 35
CHUNK_GAP_SECONDS = 1.5


def _chunk_pairs_by_gap(pairs: list) -> list[list]:
    chunks: list[list] = []
    current: list = []
    for i, p in enumerate(pairs):
        current.append(p)
        at_max = len(current) >= CHUNK_MAX_SIZE
        gap_ok = False
        if not at_max and len(current) >= CHUNK_MIN_SIZE and i + 1 < len(pairs):
            gap_ok = pairs[i + 1].target.start - p.target.end >= CHUNK_GAP_SECONDS
        if at_max or gap_ok:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _validate_scene_boundaries(scenes: list, filtered_pairs: list) -> Optional[list[list]]:
    """scenes(start_id/end_id 목록)가 filtered_pairs를 처음부터 끝까지
    순서대로 빠짐없이 겹치지 않게 커버하는지 확인한다. 하나라도 어긋나면
    (없는 id, 순서 뒤바뀜, 빈틈, 중복 등) None을 반환해 호출자가 전체를
    타임코드 공백 청킹으로 폴백하게 한다 — 일부만 살리는 부분 폴백은
    경계 접합부 처리가 그 자체로 새 버그 소스가 돼 하지 않는다."""
    id_to_index = {p.id: i for i, p in enumerate(filtered_pairs)}
    chunks: list[list] = []
    expected_start = 0
    for scene in scenes:
        start_idx = id_to_index.get(scene.get("start_id"))
        end_idx = id_to_index.get(scene.get("end_id"))
        if start_idx is None or end_idx is None:
            return None
        if start_idx != expected_start or end_idx < start_idx:
            return None
        chunks.append(filtered_pairs[start_idx:end_idx + 1])
        expected_start = end_idx + 1
    if expected_start != len(filtered_pairs):
        return None
    return chunks


async def _split_into_scenes(
    provider: ModelProvider, profile: dict, filtered_pairs: list,
    target_version_id: str, warnings: list,
) -> list[list]:
    """씬 분할 LLM 콜(GPT, json_schema 강제)로 pairs를 화제/화자/시공간/톤
    전환 기준의 문맥 단위로 나눈다. 응답이 filtered_pairs를 빠짐없이·
    순서대로·안 겹치게 커버하지 못하면(파싱 실패 포함) 1회 재시도하고,
    그래도 실패하면 타임코드 공백 청킹으로 전체 폴백한다. 성공한 씬도
    CHUNK_MAX_SIZE를 넘으면 그 안에서 다시 타임코드 공백으로 쪼갠다 —
    토큰 한도 안전장치는 씬 분할 성공 여부와 무관하게 항상 적용된다."""
    if not filtered_pairs:
        return []
    scene_items = [
        {"id": p.id, "korean_text": p.korean.text if p.korean else "",
         "target_text": p.target.text, "start": p.target.start, "end": p.target.end}
        for p in filtered_pairs
    ]
    for _attempt in range(2):
        try:
            scenes = await provider.split_scenes(scene_items, profile)
            chunks = _validate_scene_boundaries(scenes, filtered_pairs)
        except Exception:
            chunks = None
        if chunks is not None:
            return [sub for chunk in chunks for sub in _chunk_pairs_by_gap(chunk)]
    warnings.append({
        "stage": "씬 분할",
        "message": "씬 분할 실패, 타임코드 공백 기준 청킹으로 대체",
    })
    return _chunk_pairs_by_gap(filtered_pairs)


async def _run_dual_verification_pass(
    pairs: list, provider: ModelProvider, profile: dict,
    pending_sensitive_hits: list, knowledge: dict,
    format_constraint: str, target_version_id: str,
    resolved_registers: Optional[dict] = None,
) -> tuple[list, list]:
    """S2(이중 독립 검증) 패스. Claude와 GPT가 같은 원본을 동시에, 서로 뭘
    하는지 모른 채(앵커링 편향 방지) 독립적으로 검토한다 — 스페인어를 모르는
    검수자도 운영 가능해야 하므로, 두 모델의 일치 여부가 유일한 신뢰도
    신호다. 같은 줄을 둘 다 지적하면 합의 후보가 되고, 문구가 같은 뜻인지
    다시 양쪽에 교차 확인해 진짜 합의로 확정된 것만 자동 적용한다. 한쪽만
    지적했거나(불일치) 교차 확인에서 의견이 갈리면 적용하지 않고 원문을
    유지한 채 반대쪽 모델의 역번역만 참고용으로 붙인다.

    resolved_registers는 문법 필요성 판단 단계에서 이미 확정된 성별/격식
    ({segment_id: {"gender":.., "formality":..}})이다 — 확정된 줄은 그 값을
    Claude/GPT에게 같이 알려줘서, 제안하는 교정문이 문법적으로 그 성별/격식과
    어긋나지 않게 한다. 확정 안 된 줄은 여전히 아무 정보도 안 준다(추측
    금지, 기존과 동일). 반환값은 (findings, warnings)."""
    resolved_registers = resolved_registers or {}
    filtered_pairs = [p for p in pairs if p.target is not None]

    def _to_dict(p) -> dict:
        pair_dict = {"id": p.id, "korean_text": p.korean.text if p.korean else "",
                     "target_text": p.target.text}
        register = resolved_registers.get(p.id)
        if register:
            if register.get("gender"):
                pair_dict["resolved_gender"] = register["gender"]
            if register.get("formality"):
                pair_dict["resolved_formality"] = register["formality"]
        return pair_dict

    korean_text_by_id = {p.id: (p.korean.text if p.korean else "") for p in filtered_pairs}
    warnings: list = []

    async def _verify_chunk(chunk: list) -> tuple[list, list]:
        chunk_dicts = [_to_dict(p) for p in chunk]
        return await asyncio.gather(
            _safe_call(
                provider.correct_primary(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint),
                "Claude 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
            _safe_call(
                provider.verify_and_refine(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint),
                "GPT 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
        )

    scene_chunks = await _split_into_scenes(
        provider, profile, filtered_pairs, target_version_id, warnings)
    chunk_results = await asyncio.gather(*[_verify_chunk(chunk) for chunk in scene_chunks])
    claude_corrections = [c for claude_chunk, _ in chunk_results for c in claude_chunk]
    gpt_corrections = [c for _, gpt_chunk in chunk_results for c in gpt_chunk]

    candidate_pairs, claude_only, gpt_only = _reconcile_dual_verification(
        claude_corrections, gpt_corrections)

    true_agreed, disputed_claude, disputed_gpt, equivalence_warnings = await _check_equivalence(
        candidate_pairs, korean_text_by_id, provider, profile, target_version_id)
    warnings.extend(equivalence_warnings)

    all_claude_only = claude_only + disputed_claude
    all_gpt_only = gpt_only + disputed_gpt

    backtranslation_by_id, backtranslation_warnings = await _back_translate_proposals(
        provider, profile, true_agreed, all_claude_only, all_gpt_only, target_version_id)
    warnings.extend(backtranslation_warnings)

    pair_by_id = {p.id: p for p in pairs}
    findings: list = []
    for correction, status, model_label, applies in (
        *((c, "approved", "claude+gpt", True) for c in true_agreed),
        *((c, "pending", "claude", False) for c in all_claude_only),
        *((c, "pending", "gpt", False) for c in all_gpt_only),
    ):
        pair = pair_by_id.get(correction["segment_id"])
        if pair is None or pair.target is None:
            continue
        if correction["corrected_text"] == pair.target.text:
            continue
        findings.append(_make_dual_verification_finding(
            target_version_id, pair, correction, status, model_label,
            backtranslation_by_id))
        if applies:
            pair.target.text = correction["corrected_text"]
    return findings, warnings


async def _run_final_safety_net(
    pairs: list, provider: ModelProvider, target_version_id: str,
) -> tuple[list, list]:
    """S4 최종 안전망: 모든 교정이 끝난 텍스트를 기준으로 글자수·온점을 마지막
    으로 다시 검사한다 — GPT 패스가 문장을 늘리면서 새 위반을 만들 수 있어
    앞에서 한 번만 걸러서는 안 된다(design §핵심 설계 포인트: "앞에서 한 번만
    걸러선 안 됨"). 온점은 규칙 기반 자동보정이라 여기서도 LLM 없이 바로
    재적용한다. 반환값은 (final_ellipsis_violations, safety_net_findings)."""
    final_ellipsis_violations = check_ellipsis(pairs)
    final_fixed_by_segment = {v.segment_id: v.fixed_text for v in final_ellipsis_violations}
    for pair in pairs:
        if pair.id in final_fixed_by_segment:
            pair.target.text = final_fixed_by_segment[pair.id]

    line_length_violations = check_line_length(pairs)
    safety_net_findings = await shrink_violating_lines(
        pairs, line_length_violations, provider, target_version_id)
    return final_ellipsis_violations, safety_net_findings


async def run_pipeline(video_path: str, target_srt_path: str,
                        language: str, variant: str, target_version_id: str,
                        provider: ModelProvider,
                        cached_korean_segments: Optional[list] = None,
                        cached_video_proxy_path: Optional[str] = None,
                        english_srt_path: Optional[str] = None) -> dict:
    """design §전체 파이프라인의 오케스트레이터. S1(사전/규칙) → S2(Claude/GPT
    이중 독립 검증, 병렬) → S4(최종 안전망) 순서로 실행하며, 각 단계의 diff가
    findings가 된다. 오디오/영상 프록시는 STT 직후 한 번만 생성한다.

    원본 영상 삭제는 여기서 하지 않는다 — 이 함수가 반환한 뒤에도 아직 DB에
    아무것도 영속화되지 않은 상태이므로, 여기서 지우면 프로세스가 이 함수와
    호출자(background.py)의 저장 사이 어딘가에서 죽었을 때 원본도 없고 결과도
    없는 상태가 된다. 호출자가 결과를 실제로 커밋한 뒤에 지우도록
    `video_path`를 결과에 그대로 담아 돌려준다."""
    warnings: list = []
    target_segments = load_srt(target_srt_path)

    english_segments: list = []
    if english_srt_path:
        try:
            english_segments = load_srt(english_srt_path)
        except Exception as exc:
            logger.exception(
                "영어 SRT 파싱 실패, 대명사 힌트 생략하고 계속 진행 (target_version_id=%s)",
                target_version_id)
            warnings.append({"stage": "영어 SRT 대조", "message": str(exc)})

    if cached_korean_segments is not None and cached_video_proxy_path is not None:
        # Episode 단위 캐시 재사용 — 같은 화를 다른 대상언어로 다시 분석하거나
        # 재시도할 때, 이미 든 STT 비용을 또 쓰지 않는다. 최초 성공 후에는
        # 원본 영상이 삭제되므로(background.py), 캐시가 없으면 재시도 자체가
        # 불가능해질 수 있다는 점에서도 중요하다.
        korean_raw = cached_korean_segments
        video_proxy_path = cached_video_proxy_path
    else:
        # extract_audio는 subprocess.run(check=True)로 ffmpeg을 동기 호출하는,
        # 잠재적으로 몇 분씩 걸리는 CPU 바운드 작업이다. asyncio.to_thread로 감싸지
        # 않으면 이 코루틴이 FastAPI 프로세스의 이벤트 루프 자체를 막아버려 헬스체크나
        # 다른 요청까지 전부 멈춘다 — 아래 generate_video_proxy와 동일한 이유.
        wav_path = await asyncio.to_thread(extract_audio, video_path)
        try:
            # STT(네트워크 호출)와 영상 저화질 프록시 생성(로컬 ffmpeg, CPU 바운드)은
            # 서로 결과를 주고받지 않는 독립적인 작업이라 동시에 실행한다 — STT를
            # 기다리는 동안 영상 트랜스코딩도 같이 진행되어 전체 대기 시간이 줄어든다.
            # generate_video_proxy는 동기 함수라 asyncio.to_thread로 감싸 이벤트
            # 루프를 막지 않게 한다.
            #
            # return_exceptions=True로 두 작업의 완료를 항상 기다린다 — to_thread로
            # 감싼 동기 함수는 취소할 수 없으므로, transcribe가 먼저 실패해도
            # generate_video_proxy는 백그라운드 스레드에서 계속 돌아 프록시 파일을
            # 만들어낼 수 있다. 예외를 즉시 전파(gather 기본 동작)하면 그 파일이
            # 아무도 참조하지 않는 고아로 남는다 — 아래에서 결과를 직접 검사해
            # 그런 파일이 생겼으면 지우고 나서 실패를 전파한다.
            korean_raw, video_proxy_path = await asyncio.gather(
                _transcribe_in_chunks(provider, wav_path),
                asyncio.to_thread(generate_video_proxy, video_path),
                return_exceptions=True,
            )
        finally:
            # STT가 WAV를 다 읽은 뒤(성공/실패 무관)에는 더 이상 필요 없다. 2시간
            # 분량 영화의 16kHz mono WAV는 ~230MB에 달해, 지우지 않으면 영상
            # 프록시 기능의 스토리지 절감 취지가 무색해진다.
            Path(wav_path).unlink(missing_ok=True)

        if isinstance(korean_raw, Exception) or isinstance(video_proxy_path, Exception):
            if not isinstance(video_proxy_path, Exception) and video_proxy_path:
                Path(video_proxy_path).unlink(missing_ok=True)
            raise korean_raw if isinstance(korean_raw, Exception) else video_proxy_path

    korean_segments = [SegmentText(**s) for s in korean_raw]

    pairs = align(korean_segments, target_segments)

    # 온점 자동보정은 다른 모든 단계보다 먼저 적용한다 — 이후 단계가 보정된
    # 텍스트를 기준으로 작업하도록.
    ellipsis_violations = check_ellipsis(pairs)
    fixed_by_segment = {v.segment_id: v.fixed_text for v in ellipsis_violations}
    for pair in pairs:
        if pair.id in fixed_by_segment:
            pair.target.text = fixed_by_segment[pair.id]

    profile = load_profile(language, variant)
    knowledge = load_knowledge()
    sensitive_terms = load_sensitive_terms()
    glossary = load_glossary()
    cta_patterns = load_cta_patterns()
    profanity_dictionary = load_profanity_dictionary()

    pretreatment = run_pretreatment(
        pairs, glossary, cta_patterns, profanity_dictionary, sensitive_terms,
        target_version_id,
    )
    pairs = pretreatment.pairs

    segment_resolutions, grammar_warnings = await _run_grammar_necessity_check(
        pairs, profile, english_segments, target_version_id,
    )
    warnings.extend(grammar_warnings)

    # 문법 필요성 판단이 한국어 원문/영어 SRT로 이미 확정한 성별/격식을
    # AI 검증 단계에 넘긴다 — 그래야 교정 제안이 그 성별/격식과 문법적으로
    # 어긋나지 않는다(design §관계에 맞는 문장을 추천). 확정 안 된 줄은
    # 여기 아예 안 들어가므로 AI는 여전히 아무것도 추측하지 않는다.
    resolved_registers = {
        r["segment_id"]: {"gender": r.get("resolved_gender"), "formality": r.get("resolved_formality")}
        for r in segment_resolutions
        if r.get("resolved_gender") or r.get("resolved_formality")
    }

    format_constraint = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."

    dual_verification_findings, dual_verification_warnings = await _run_dual_verification_pass(
        pairs, provider, profile,
        pretreatment.pending_sensitive_hits, knowledge, format_constraint,
        target_version_id, resolved_registers,
    )
    warnings.extend(dual_verification_warnings)

    final_ellipsis_violations, safety_net_findings = await _run_final_safety_net(
        pairs, provider, target_version_id,
    )
    # line_length_violations는 여기 담아 반환하지 않는다 — shrink_violating_lines가
    # 이미 텍스트를 줄이고 그 결과를 category="formatting" Finding으로 반환했다.
    # 그런데도 여기 다시 담으면 repositories.py가 "이미 줄어든 뒤" 텍스트를
    # original_text로 삼아 별도의 pending formatting finding을 하나 더 만든다 —
    # 검수자에게 사실과 다른(더 이상 위반이 아닌) 원문을 보여주는 중복 레코드다.
    # 온점 위반은 safety_net 같은 별도 Finding 생성 경로가 없는(규칙 기반
    # 자동보정이 전부인) 경우라 여기서 그대로 반환해야 한다.
    format_violations = ellipsis_violations + final_ellipsis_violations

    return {
        "pairs": pairs,
        "format_violations": format_violations,
        "segment_resolutions": segment_resolutions,
        "video_path": video_path,
        "video_proxy_path": video_proxy_path,
        "korean_segments_raw": korean_raw,
        "warnings": warnings,
        "findings": (
            pretreatment.findings + dual_verification_findings + safety_net_findings
        ),
    }
