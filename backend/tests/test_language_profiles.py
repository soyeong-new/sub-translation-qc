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


def test_load_profile_rejects_path_traversal_in_language():
    """Test that path-traversal characters in language parameter are rejected"""
    with pytest.raises(FileNotFoundError):
        load_profile("../../etc", "LATAM")
    with pytest.raises(FileNotFoundError):
        load_profile("es/test", "LATAM")
    with pytest.raises(FileNotFoundError):
        load_profile("es..passwd", "LATAM")


def test_load_profile_rejects_path_traversal_in_variant():
    """Test that path-traversal characters in variant parameter are rejected"""
    with pytest.raises(FileNotFoundError):
        load_profile("es", "../../etc")
    with pytest.raises(FileNotFoundError):
        load_profile("es", "LATAM/test")
    with pytest.raises(FileNotFoundError):
        load_profile("es", "LATAM..passwd")


def test_load_profile_rejects_numeric_and_special_chars():
    """Test that numeric and special characters are rejected"""
    with pytest.raises(FileNotFoundError):
        load_profile("es123", "LATAM")
    with pytest.raises(FileNotFoundError):
        load_profile("es", "LATAM-br")
    with pytest.raises(FileNotFoundError):
        load_profile("es-ES", "LATAM")
