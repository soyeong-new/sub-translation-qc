import pytest
from unittest.mock import AsyncMock
from app.core.requery import requery_finding, RequeryNotSupportedError
from app.models import FindingRow, Segment


def _finding(model, suggested_text="hola corregido"):
    return FindingRow(id="f1", target_version_id="tv1", segment_id="seg1",
                       category="translation", description="근거",
                       original_text="hola", suggested_text=suggested_text,
                       confidence=0.9, model=model, status="pending")


def _segment():
    return Segment(id="seg1", target_version_id="tv1", index=0, start=0.0, end=1.0,
                   korean_text="안녕", target_text="hola")


class _StubProvider:
    def __init__(self):
        self.correct_primary = AsyncMock(
            return_value=[{"segment_id": "seg1", "category": "translation",
                            "corrected_text": "hola más formal", "description": "재질문 반영"}])
        self.verify_and_refine = AsyncMock(
            return_value=[{"segment_id": "seg1", "category": "translation",
                            "corrected_text": "hola verificado", "description": "재질문 반영"}])
        self.shrink_line = AsyncMock(return_value="hola corto")


@pytest.mark.asyncio
async def test_requery_claude_finding_calls_correct_primary_with_instruction():
    provider = _StubProvider()
    result = await requery_finding(_finding("claude"), _segment(), "더 격식있게",
                                    provider, knowledge="", profile={})
    assert result == "hola más formal"
    provider.correct_primary.assert_awaited_once()
    assert provider.correct_primary.call_args.kwargs["extra_instruction"] == "더 격식있게"


@pytest.mark.asyncio
async def test_requery_gpt_finding_calls_verify_and_refine_with_instruction():
    provider = _StubProvider()
    result = await requery_finding(_finding("gpt"), _segment(), "직역투 다시 봐줘",
                                    provider, knowledge="", profile={})
    assert result == "hola verificado"
    provider.verify_and_refine.assert_awaited_once()


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
    provider.correct_primary = AsyncMock(return_value=[])
    result = await requery_finding(_finding("claude", suggested_text="hola corregido"),
                                    _segment(), "더 격식있게", provider,
                                    knowledge="", profile={})
    assert result == "hola corregido"
    provider.correct_primary.assert_awaited_once()


@pytest.mark.asyncio
async def test_requery_finding_passes_resolved_character_gender_to_claude(monkeypatch):
    from unittest.mock import AsyncMock
    from app.models import FindingRow, Segment, Character
    from app.core.requery import requery_finding

    finding = FindingRow(id="f1", target_version_id="tv1", segment_id="s1",
                         category="gender", description="d", original_text="a",
                         suggested_text="b", confidence=1.0, model="claude", status="pending")
    segment = Segment(id="s1", target_version_id="tv1", index=0, start=0.0, end=1.0,
                      korean_text="안녕", target_text="hola",
                      resolved_character_id="c1")
    resolved_character = {"id": "c1", "label": "민지", "confirmed_gender": "female"}

    provider = type("P", (), {})()
    provider.correct_primary = AsyncMock(
        return_value=[{"segment_id": "s1", "corrected_text": "hola corregido"}])

    result = await requery_finding(
        finding, segment, "다시 확인해줘", provider, knowledge="", profile={},
        resolved_character=resolved_character, resolved_relationship=None,
    )

    assert result == "hola corregido"
    call_kwargs = provider.correct_primary.call_args
    characters_arg = call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs.get("characters")
    assert characters_arg == [resolved_character]
