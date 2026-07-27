import pytest
from app.schemas import AlignedPair, SegmentText
from app.core.character_registry import build_registry
from app.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_build_registry_runs_when_any_check_enabled():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="Hola"))]
    profile = {"checks_enabled": {"gender_agreement": True, "register_consistency": False}}
    result = await build_registry(pairs, profile, MockProvider())
    assert result["characters"] == [{"label": "인물1", "gendered_segment_ids": []}]


@pytest.mark.asyncio
async def test_build_registry_skips_when_no_check_enabled():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="Hola"))]
    profile = {"checks_enabled": {"gender_agreement": False, "register_consistency": False}}
    result = await build_registry(pairs, profile, MockProvider())
    assert result == {"characters": [], "relationships": []}
