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
            line = f"- {rule.get('term', '')}: {rule.get('rule', '')}"
            # bad/good은 특정 언어 예문이라(예: 스페인어) 다른 언어에도 그대로
            # 섞여 들어가면 안 된다 — 언어 무관 규칙만 담는 이 지식베이스에는
            # 아예 없는 게 정상이라, 있을 때만 붙인다(빈 "-" 표시로 프롬프트
            # 토큰을 낭비하지 않는다).
            if rule.get("bad") or rule.get("good"):
                line += f" (나쁜 예: {rule.get('bad', '-')} / 좋은 예: {rule.get('good', '-')})"
            lines.append(line)
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
