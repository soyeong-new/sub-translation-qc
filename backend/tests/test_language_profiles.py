import pytest
from app.language_profiles.loader import load_profile


def test_load_es_latam_profile_has_expected_shape():
    profile = load_profile("es", "LATAM")
    assert profile["language"] == "es"
    assert profile["checks_enabled"]["gender_agreement"] is True
    assert profile["checks_enabled"]["register_consistency"] is True
    assert profile["register_system"]["formality_mapping"]["formal"] == "usted"
    assert profile["register_system"]["formality_mapping"]["informal"] == "tú"


def test_load_profile_raises_for_unknown_language():
    with pytest.raises(FileNotFoundError):
        load_profile("xx", "YYYY")
