"""파이프라인 실행 결과(세그먼트/findings)를 DB에 영속화하는 모듈."""

from typing import List
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import FindingRow, Segment, SttCorrection, GenderWordResolution
from app.schemas import Finding


def _ns(target_version_id: str, local_id: str) -> str:
    """파이프라인 내부 ID(예: "pair_1", "finding_pair_1_translation")를
    target_version 단위로 네임스페이싱한다.

    alignment.align()은 실행마다 1부터 다시 세는 카운터로 pair.id를 만들기
    때문에("pair_1", "pair_2", ...) 서로 다른 target_version의 결과를 같은 DB에
    저장하면 segments/findings의 전역 PK가 충돌한다. ID 생성 규칙 자체는
    파이프라인 한 번의 실행 안에서만 쓰이므로 그대로 두고, 영속화 시점에만
    prefix를 붙여 전역 유일성을 확보한다."""
    return f"{target_version_id}:{local_id}"


def _save_findings(session: AsyncSession, target_version_id: str, findings: list) -> None:
    for f in findings:
        session.add(FindingRow(
            id=_ns(target_version_id, f.id), target_version_id=target_version_id,
            segment_id=_ns(target_version_id, f.segment_id),
            category=f.category, description=f.description,
            original_text=f.original_text, suggested_text=f.suggested_text,
            confidence=f.confidence, source=f.source, model=f.model, status=f.status,
            final_text=f.final_text, reviewer_name=f.reviewer_name,
        ))


async def _save_format_violations(session: AsyncSession, target_version_id: str,
                                   violations: list, target_text_by_pair: dict) -> None:
    # 포맷 위반은 FormatViolation(스키마)이라 Finding과 필드가 1:1로 대응하지
    # 않는다. category="formatting" FindingRow로 변환해 검수 UI가 다른 카테고리와
    # 동일하게 다룰 수 있게 한다. 한 세그먼트에서 온점 위반이 최초 체크(phase1)와
    # GPT 2차 이후 최종 재체크(phase2)에서 각각 걸릴 수 있어(같은 rule="ellipsis"),
    # 두 번째부터는 id에 _2를 붙여 PK 충돌을 막는다 — phase1/phase2가 서로 다른
    # 트랜잭션으로 저장되므로 이번 호출 안의 카운터만으로는 부족해, 같은
    # target_version에 이미 저장된 id를 먼저 조회해 겹치는지 확인한다.
    #
    # original_text는 target_text_by_pair(파이프라인이 그 단계까지 끝난 뒤의
    # 상태)로 되짚어 재구성하지 않는다 — 대신 FormatViolation.original_text에
    # 각 check_* 함수가 검사 시점에 직접 남긴 스냅샷을 그대로 쓴다. 레거시/직접
    # 구성된 FormatViolation(그 필드를 비워 둔 경우)만 폴백으로 쓴다.
    if not violations:
        return
    existing_ids = set((await session.execute(
        select(FindingRow.id).where(FindingRow.target_version_id == target_version_id)
    )).scalars().all())
    for v in violations:
        base_id = _ns(target_version_id, f"finding_{v.segment_id}_formatting_{v.rule}")
        final_id = f"{base_id}_2" if base_id in existing_ids else base_id
        existing_ids.add(final_id)
        # 자동보정된 위반(온점 4개 이상)은 판단 여지가 없는 기계적 규칙이라
        # 파이프라인이 이미 텍스트에 적용해 놓은 상태다. 검수자가 결정할 것이
        # 남아 있지 않으므로 대기열에 쌓아 두지 않고 바로 approved로 확정한다.
        # 반면 줄 길이 위반은 의미를 보존하며 문장을 줄이는 판단이 필요하므로
        # (format_rules.check_line_length가 자동 수정을 하지 않는 이유와 동일)
        # pending으로 남겨 검수자에게 넘긴다.
        suggested_text = v.fixed_text if v.auto_fixed else ""
        original_text = v.original_text or target_text_by_pair.get(v.segment_id, "")
        session.add(FindingRow(
            id=final_id, target_version_id=target_version_id,
            segment_id=_ns(target_version_id, v.segment_id),
            category="formatting", description=v.detail,
            original_text=original_text,
            suggested_text=suggested_text,
            confidence=1.0, source="rule",
            status="approved" if v.auto_fixed else "pending",
            final_text=suggested_text if v.auto_fixed else "",
        ))


def _save_segments(session: AsyncSession, target_version_id: str,
                    pairs: list, segment_resolutions: list) -> None:
    # findings.segment_id가 segments.id를 참조하는 FK이므로, 네임스페이싱된
    # pair.id를 Segment.id로 써서 findings가 참조할 수 있게 한다.
    resolution_by_segment: dict = {r["segment_id"]: r for r in segment_resolutions}
    for index, pair in enumerate(pairs):
        if pair.target is not None:
            start, end = pair.target.start, pair.target.end
        elif pair.korean is not None:
            start, end = pair.korean.start, pair.korean.end
        else:
            start, end = 0.0, 0.0
        resolution = resolution_by_segment.get(pair.id, {})
        session.add(Segment(
            id=_ns(target_version_id, pair.id), target_version_id=target_version_id,
            index=index, start=start, end=end,
            korean_text=pair.korean.text if pair.korean else "",
            target_text=pair.target.text if pair.target else "",
            gender_check_needed=bool(resolution.get("gender_check_needed")),
            formality_check_needed=bool(resolution.get("formality_check_needed")),
            # 한국어 원문(어미/호칭)이나 영어 SRT로 이미 자동 판정된 값 —
            # 검수자가 스테퍼에서 또 묻지 않아도 되게 미리 채운다(design
            # §정말 판단하기 어려운 것만 질문). 자동 판정 못 했으면 None
            # 그대로라 지금까지처럼 사람이 확인해야 한다.
            resolved_gender_raw=resolution.get("resolved_gender"),
            resolved_formality_raw=resolution.get("resolved_formality"),
            english_pronoun_hint=resolution.get("english_pronoun_hint"),
            # 한 줄에 인물이 둘 이상이면 채워진다(design §인물별로 따로
            # 확인) — resolved_gender_raw는 이 경우 쓰지 않는다.
            resolved_gender_groups_raw=resolution.get("resolved_gender_groups"),
        ))


async def save_phase1_result(session: AsyncSession, target_version_id: str,
                              result: dict) -> None:
    """S1(STT/정렬/사전·규칙 처리/문법 필요성 판단) 결과를 저장한다 — Segment
    행 생성 + 사전필터 findings + 최초 온점 자동보정. 성별/격식 확인이 필요한
    줄이 있으면 이 저장이 끝난 뒤 사람 확인을 기다리고, S2(AI 검증)는 아직
    실행되지 않은 상태다."""
    _save_segments(session, target_version_id, result["pairs"], result.get("segment_resolutions", []))

    # 명시적 flush: segments를 findings보다 먼저 INSERT해야
    # findings.segment_id의 FK 제약이 통과한다 (두 모델 간 relationship()이
    # 없어 세션의 자동 의존성 정렬만으로는 순서가 보장되지 않았다).
    await session.flush()

    _save_findings(session, target_version_id, result["findings"])

    target_text_by_pair = {
        p.id: (p.target.text if p.target else "") for p in result["pairs"]
    }
    await _save_format_violations(
        session, target_version_id, result.get("format_violations", []), target_text_by_pair)


async def save_phase2_result(session: AsyncSession, target_version_id: str,
                              result: dict) -> None:
    """S2(Claude/GPT 이중 독립 검증) + S4(최종 안전망) 결과를 저장한다.
    save_phase1_result가 이미 만든 Segment 행이 존재한다고 가정한다."""
    _save_findings(session, target_version_id, result["findings"])

    target_text_by_pair = {
        p.id: (p.target.text if p.target else "") for p in result["pairs"]
    }
    await _save_format_violations(
        session, target_version_id, result.get("format_violations", []), target_text_by_pair)


async def save_pipeline_result(session: AsyncSession, target_version_id: str,
                                result: dict) -> None:
    """phase1 + phase2가 한 번에 합쳐진 결과(run_pipeline 편의 래퍼의 반환값)를
    한 번에 저장하는 하위 호환 함수 — 테스트/스크립트가 단일 호출로 쓰던 기존
    시그니처를 유지한다. save_phase1_result/save_phase2_result를 그대로
    이어 부르지 않는다 — 그러면 이미 합쳐진 findings/format_violations
    리스트를 두 번 저장해 PK가 충돌한다."""
    _save_segments(session, target_version_id, result["pairs"], result.get("segment_resolutions", []))
    await session.flush()

    _save_findings(session, target_version_id, result["findings"])

    target_text_by_pair = {
        p.id: (p.target.text if p.target else "") for p in result["pairs"]
    }
    await _save_format_violations(
        session, target_version_id, result.get("format_violations", []), target_text_by_pair)


async def delete_target_version_results(session: AsyncSession, target_version_id: str) -> None:
    """재분석(재시도) 전에 기존 Segment/FindingRow/SttCorrection을 지운다. 이게
    없으면 save_pipeline_result가 이전 실행과 동일한 네임스페이싱된 ID로 다시
    INSERT를 시도해 PK 충돌(IntegrityError)로 실패한다.
    findings.segment_id와 stt_corrections.segment_id가 모두 segments.id를
    참조하는 하드 FK(ondelete 없음)이므로, 둘 다 Segment보다 먼저 지워야 한다
    (POST /segments/{id}/correct-stt는 분석 상태와 무관하게 SttCorrection을
    만들 수 있어, 이걸 빼먹으면 findings와 동일한 FK 위반이 재현된다).
    FindingRow와 SttCorrection 사이에는 순서 제약이 없다 — 서로를 참조하지
    않는다."""
    await session.execute(
        delete(FindingRow).where(FindingRow.target_version_id == target_version_id)
    )
    await session.execute(
        delete(SttCorrection).where(
            SttCorrection.segment_id.in_(
                select(Segment.id).where(Segment.target_version_id == target_version_id)
            )
        )
    )
    await session.execute(
        delete(Segment).where(Segment.target_version_id == target_version_id)
    )
    await session.flush()


async def get_findings(session: AsyncSession, target_version_id: str) -> List[FindingRow]:
    # ORDER BY 없이는 정렬 순서가 보장되지 않는다 — 특히 UPDATE(승인/거부/
    # 수정/재질문) 후에는 순서가 바뀔 수 있어, 검수자가 방금 수정한 카드가
    # 다른 위치로 옮겨가 "없어진 것처럼" 보이는 문제가 있었다. Segment.index
    # (영상 안에서의 실제 순번)로 정렬해 영상 순서와 일치시키고, id를 같은
    # 세그먼트에 finding이 여러 개일 때의 안정적 타이브레이커로 쓴다.
    # segment_id 문자열 정렬은 "pair_10"이 "pair_2"보다 앞에 오는 등 실제
    # 순서와 안 맞았다.
    rows = await session.execute(
        select(FindingRow).join(Segment, FindingRow.segment_id == Segment.id)
        .where(FindingRow.target_version_id == target_version_id)
        .order_by(Segment.index, FindingRow.id)
    )
    return list(rows.scalars().all())


async def get_pending_findings_for_segment(session: AsyncSession, segment_id: str) -> List[FindingRow]:
    rows = await session.execute(
        select(FindingRow).where(
            FindingRow.segment_id == segment_id, FindingRow.status == "pending")
    )
    return list(rows.scalars().all())


async def get_findings_for_segment(session: AsyncSession, segment_id: str) -> List[FindingRow]:
    """상태 무관하게 이 세그먼트의 finding 전부를 돌려준다 — STT 재검증이
    새 카드를 만들지, 기존 카드를 갱신할지 판단하려면 지금 몇 개나 있는지
    먼저 알아야 한다."""
    rows = await session.execute(
        select(FindingRow).where(FindingRow.segment_id == segment_id)
    )
    return list(rows.scalars().all())


async def get_suggested_not_applicable_lemmas(session: AsyncSession, language: str) -> set:
    """이 언어에서 지금까지 "해당 없음"으로만 판정되고 한 번도 실제 성별
    (male/female)로 판정된 적 없는 단어 기본형(lemma) 집합을 돌려준다.
    질문 자체를 숨기는 데는 쓰지 않는다 — 한 번 숨기면 그 뒤로 반증 사례를
    영영 못 잡기 때문이다(같은 단어가 다른 영화에서는 실제로 사람을
    가리킬 수도 있음). "해당 없음" 버튼에 추천 표시만 하는 용도로만 쓴다."""
    rows = await session.execute(
        select(GenderWordResolution.word_lemma, GenderWordResolution.resolution)
        .where(GenderWordResolution.language == language)
    )
    resolutions_by_lemma: dict = {}
    for lemma, resolution in rows.all():
        resolutions_by_lemma.setdefault(lemma, set()).add(resolution)
    return {
        lemma for lemma, resolutions in resolutions_by_lemma.items()
        if resolutions == {"not_applicable"}
    }


async def record_gender_word_resolution(
    session: AsyncSession, language: str, word_lemmas: List[str], resolution: str,
) -> None:
    """검수자가 성별 표시 단어에 실제로 어떻게 답했는지(male/female/
    not_applicable) 기록한다 — 다음 프로젝트에서 같은 단어의 "해당 없음"
    추천 여부를 판단하는 근거가 된다."""
    for lemma in word_lemmas:
        session.add(GenderWordResolution(language=language, word_lemma=lemma, resolution=resolution))
