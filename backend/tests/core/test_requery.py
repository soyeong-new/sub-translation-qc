import pytest
from unittest.mock import AsyncMock
from app.core.requery import (
    requery_finding, apply_resolved_gender_to_text,
    RequeryNotSupportedError, RequeryNoResultError,
)
from app.models import FindingRow, Segment
from app.providers.mock import MockProvider


def _finding(model, suggested_text="hola corregido"):
    return FindingRow(id="f1", target_version_id="tv1", segment_id="seg1",
                       category="mistranslation", description="근거",
                       original_text="hola", suggested_text=suggested_text,
                       confidence=0.9, model=model, status="pending")


def _segment():
    return Segment(id="seg1", target_version_id="tv1", index=0, start=0.0, end=1.0,
                   korean_text="안녕", target_text="hola")


class _StubProvider:
    def __init__(self):
        self.correct_primary = AsyncMock(
            return_value=[{"segment_id": "seg1", "category": "mistranslation",
                            "corrected_text": "hola verificado (claude)", "description": "재질문 반영",
                            "back_translation": "안녕 확인됨 (claude)"}])
        self.verify_and_refine = AsyncMock(
            return_value=[{"segment_id": "seg1", "category": "mistranslation",
                            "corrected_text": "hola verificado", "description": "재질문 반영",
                            "back_translation": "안녕 확인됨"}])
        self.shrink_line = AsyncMock(return_value="hola corto")


@pytest.mark.asyncio
async def test_requery_claude_finding_calls_correct_primary_with_instruction():
    """model="claude" finding은 claude에게, gpt/claude+gpt finding은 gpt에게
    다시 묻는다 — 원래 그 문제를 지적한 모델이 검수자의 추가 지시를 반영해
    재검토하는 게, 지적하지도 않은 모델에게 대신 묻는 것보다 타당하다."""
    provider = _StubProvider()
    result = await requery_finding(_finding("claude"), _segment(), "직역투 다시 봐줘",
                                    provider, knowledge="", profile={})
    assert result == ("hola verificado (claude)", "안녕 확인됨 (claude)")
    provider.correct_primary.assert_awaited_once()
    assert provider.correct_primary.call_args.kwargs["extra_instruction"] == "직역투 다시 봐줘"
    provider.verify_and_refine.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt", "claude+gpt"])
async def test_requery_gpt_or_consensus_finding_calls_verify_and_refine_with_instruction(model):
    provider = _StubProvider()
    result = await requery_finding(_finding(model), _segment(), "직역투 다시 봐줘",
                                    provider, knowledge="", profile={})
    assert result == ("hola verificado", "안녕 확인됨")
    provider.verify_and_refine.assert_awaited_once()
    assert provider.verify_and_refine.call_args.kwargs["extra_instruction"] == "직역투 다시 봐줘"
    provider.correct_primary.assert_not_awaited()


@pytest.mark.asyncio
async def test_requery_safety_net_finding_calls_shrink_line():
    provider = _StubProvider()
    result = await requery_finding(_finding("안전망"), _segment(), "더 짧게",
                                    provider, knowledge="", profile={})
    assert result == ("hola corto", None)
    provider.shrink_line.assert_awaited_once()


@pytest.mark.asyncio
async def test_requery_rule_based_finding_raises_not_supported():
    provider = _StubProvider()
    with pytest.raises(RequeryNotSupportedError):
        await requery_finding(_finding("사전필터"), _segment(), "다시 봐줘",
                               provider, knowledge="", profile={})


@pytest.mark.asyncio
async def test_requery_null_model_finding_raises_not_supported():
    provider = _StubProvider()
    with pytest.raises(RequeryNotSupportedError):
        await requery_finding(_finding(None), _segment(), "다시 봐줘",
                               provider, knowledge="", profile={})


@pytest.mark.asyncio
async def test_requery_raises_when_provider_returns_no_results():
    """LLM이 빈 배열을 돌려주면(회귀: 프롬프트가 재질문 지시와 충돌해 스킵한
    경우) 예전에는 current_text를 그대로 반환해 검수자에게 "재질문이 아무
    효과도 없었다"는 게 조용히 묻혔다 — 이제는 명시적으로 실패를 알려야
    한다."""
    provider = _StubProvider()
    provider.correct_primary = AsyncMock(return_value=[])
    with pytest.raises(RequeryNoResultError):
        await requery_finding(_finding("claude", suggested_text="hola corregido"),
                               _segment(), "더 격식있게", provider,
                               knowledge="", profile={})
    provider.correct_primary.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_resolved_gender_to_text_handles_legacy_groups_without_candidate_indices():
    """회귀: 이 브랜치 이전에 저장된 resolved_gender_groups_raw 행은
    candidate_indices 키가 없다(words/target_word_lemmas/gender만 있음).
    이런 옛 행을 만나면 KeyError로 죽는 대신, 인덱스 없는 그룹은 안전하게
    "아무 단어에도 적용 안 함"으로 처리해 원문을 그대로 돌려줘야 한다."""
    segment = _segment()
    segment.resolved_gender_groups_raw = [
        {"words": ["guapo"], "target_word_lemmas": ["guapo"], "gender": "male"},
    ]
    result = await apply_resolved_gender_to_text(
        segment, "es una persona guapa", _StubProvider(), {"language": "es"})
    assert result == "es una persona guapa"


@pytest.mark.asyncio
async def test_apply_resolved_gender_to_text_rolls_back_swap_flagged_as_grammatically_broken(monkeypatch):
    """회귀(실사용 재현): STT 재검증/다시 질문하기 제안문구에 성별을 재반영하는
    이 경로는 pipeline._apply_resolved_gender와 달리 verify_gender_swap
    안전망을 안 거치고 있었다 — spaCy 구조 오탐("la caja"를 서술명사로
    오분석해 "la cajo"로 깨뜨리는 경우)이 그대로 새어나갔다. 다른 경로와
    동일하게 문법이 깨졌다고 판정되면 치환 전 텍스트로 롤백해야 한다."""
    segment = _segment()
    segment.resolved_gender_raw = "male"
    provider = MockProvider()

    async def _flag_error(items, profile):
        return [{"id": i["id"], "has_error": True} for i in items]

    monkeypatch.setattr(provider, "verify_gender_swap", _flag_error)

    result = await apply_resolved_gender_to_text(
        segment, "El recibo está en la caja.", provider, {"language": "es"})
    assert result == "El recibo está en la caja."
