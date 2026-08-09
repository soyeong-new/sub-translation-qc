import pytest
from unittest.mock import AsyncMock
from app.core.requery import requery_finding, RequeryNotSupportedError
from app.models import FindingRow, Segment


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
        self.verify_and_refine = AsyncMock(
            return_value=[{"segment_id": "seg1", "category": "mistranslation",
                            "corrected_text": "hola verificado", "description": "재질문 반영"}])
        self.shrink_line = AsyncMock(return_value="hola corto")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude", "gpt", "claude+gpt"])
async def test_requery_llm_finding_calls_verify_and_refine_with_instruction(model):
    """finding.model이 뭐였든(claude/gpt/claude+gpt) 재질문은 GPT 단일
    모델(verify_and_refine)만 쓴다 — 검수자가 이미 지시사항으로 방향을
    정했으니 원래의 이중 독립검증(합의 필요)까지는 안 해도 된다."""
    provider = _StubProvider()
    result = await requery_finding(_finding(model), _segment(), "직역투 다시 봐줘",
                                    provider, knowledge="", profile={})
    assert result == "hola verificado"
    provider.verify_and_refine.assert_awaited_once()
    assert provider.verify_and_refine.call_args.kwargs["extra_instruction"] == "직역투 다시 봐줘"


@pytest.mark.asyncio
async def test_requery_safety_net_finding_calls_shrink_line():
    provider = _StubProvider()
    result = await requery_finding(_finding("안전망"), _segment(), "더 짧게",
                                    provider, knowledge="", profile={})
    assert result == "hola corto"
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
async def test_requery_returns_current_text_unchanged_when_provider_returns_no_results():
    provider = _StubProvider()
    provider.verify_and_refine = AsyncMock(return_value=[])
    result = await requery_finding(_finding("claude", suggested_text="hola corregido"),
                                    _segment(), "더 격식있게", provider,
                                    knowledge="", profile={})
    assert result == "hola corregido"
    provider.verify_and_refine.assert_awaited_once()
