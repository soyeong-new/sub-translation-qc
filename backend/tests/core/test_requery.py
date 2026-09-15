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
async def test_requery_reverts_canonical_regression_when_glossary_entries_given():
    """재질문도 1차 AI 검증(pipeline._make_dual_verification_finding)과 같은
    문제를 겪는다 — LLM이 등록된 고유명사 표기를 미등록 표기로 바꿔버릴 수
    있다. finding.original_text에 이미 정확한 표기가 있었다면 재질문
    결과도 그 표기로 되돌려야 한다(실사용 재현: 강옥/강오크 → Kang Hulk가
    재질문에서도 Kang Ok로 되돌아가지 않던 문제)."""
    provider = _StubProvider()
    provider.correct_primary = AsyncMock(
        return_value=[{"segment_id": "seg1", "category": "mistranslation",
                        "corrected_text": "Oye, Kang Ok.", "description": "재질문 반영",
                        "back_translation": None}])
    finding = _finding("claude", suggested_text="Oye, Kang Ok.")
    finding.original_text = "Oye, Kang Hulk."
    segment = _segment()
    segment.korean_text = "야, 강옥!"
    glossary_entries = [{"entry_id": "e1", "korean_term": "강옥", "category": "person",
                          "aliases": [], "canonical": "Kang Hulk"}]

    result = await requery_finding(finding, segment, "표기 다시 확인해줘", provider,
                                    knowledge="", profile={}, glossary_entries=glossary_entries)

    assert result == ("Oye, Kang Hulk.", None)


@pytest.mark.asyncio
async def test_apply_resolved_gender_to_text_applies_confirmed_group_gender():
    """1차 검수 때 확정된 다인물 그룹 성별이 STT 재검증 등에서 나중에 생긴
    텍스트에도 apply_gender_groups를 통해 반영돼야 한다."""
    segment = _segment()
    segment.resolved_gender_groups_raw = [
        {"words": ["guapo"], "referent": "화자", "gender": "male"},
    ]
    result = await apply_resolved_gender_to_text(
        segment, "es una persona guapa", MockProvider(), {"language": "es"})
    assert result == "[male] es una persona guapa"


@pytest.mark.asyncio
async def test_apply_resolved_gender_to_text_applies_confirmed_single_gender():
    """인물이 하나뿐이면(그룹 없이 단일값) apply_gender를 통해 반영돼야
    한다."""
    segment = _segment()
    segment.resolved_gender_raw = "male"

    result = await apply_resolved_gender_to_text(
        segment, "El recibo está en la caja.", MockProvider(), {"language": "es"})
    assert result == "[male] El recibo está en la caja."
