import pytest
from app.language_profiles.loader import load_profile, list_profiles


def test_load_es_latam_profile_has_expected_shape():
    profile = load_profile("es", "LATAM")
    assert profile["language"] == "es"
    assert profile["variant"] == "LATAM"
    assert "직역투" in profile["naturalness_check"]["llm_instruction"]


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


def test_list_profiles_includes_es_latam():
    profiles = list_profiles()
    assert {"language": "es", "variant": "LATAM"} in profiles


def test_load_pt_br_profile_has_expected_shape():
    profile = load_profile("pt", "BR")
    assert profile["language"] == "pt"
    assert profile["variant"] == "BR"
    assert "포르투갈어" in profile["naturalness_check"]["llm_instruction"]
    assert "você" in profile["formality_instruction"]


def test_list_profiles_includes_pt_br():
    profiles = list_profiles()
    assert any(p["language"] == "pt" and p["variant"] == "BR" for p in profiles)


def test_list_profiles_ignores_non_yaml_files(tmp_path, monkeypatch):
    import app.language_profiles.loader as loader_module
    monkeypatch.setattr(loader_module, "_DIR", tmp_path)
    (tmp_path / "es_LATAM.yaml").write_text("language: es\nvariant: LATAM\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a profile", encoding="utf-8")
    profiles = loader_module.list_profiles()
    assert profiles == [{"language": "es", "variant": "LATAM"}]
