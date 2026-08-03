"""호칭/관용구 지식베이스와 민감어 사전을 YAML에서 불러오는 모듈."""

from pathlib import Path
from typing import List, Optional
import yaml

_DEFAULT_DIR = Path(__file__).parent


def load_knowledge(dir_path: Optional[str] = None) -> str:
    base = Path(dir_path) if dir_path else _DEFAULT_DIR
    lines = []
    for yml in sorted(base.glob("*.yaml")):
        if yml.name in ("sensitive_terms.yaml", "glossary.yaml", "cta_patterns.yaml",
                        "profanity_dictionary.yaml"):
            continue
        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        for rule in data.get("rules", []):
            lines.append(
                f"- {rule.get('term', '')}: {rule.get('rule', '')}"
                f" (나쁜 예: {rule.get('bad', '-')} / 좋은 예: {rule.get('good', '-')})"
            )
    return "\n".join(lines)


def load_sensitive_terms(dir_path: Optional[str] = None) -> List[str]:
    base = Path(dir_path) if dir_path else _DEFAULT_DIR
    path = base / "sensitive_terms.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("terms", [])


def load_glossary(dir_path: Optional[str] = None) -> List[dict]:
    base = Path(dir_path) if dir_path else _DEFAULT_DIR
    data = yaml.safe_load((base / "glossary.yaml").read_text(encoding="utf-8")) or {}
    return data.get("entries", [])


def load_cta_patterns(dir_path: Optional[str] = None) -> List[str]:
    base = Path(dir_path) if dir_path else _DEFAULT_DIR
    data = yaml.safe_load((base / "cta_patterns.yaml").read_text(encoding="utf-8")) or {}
    return data.get("patterns", [])


def load_profanity_dictionary(dir_path: Optional[str] = None) -> List[dict]:
    base = Path(dir_path) if dir_path else _DEFAULT_DIR
    data = yaml.safe_load((base / "profanity_dictionary.yaml").read_text(encoding="utf-8")) or {}
    return data.get("entries", [])
