from pathlib import Path
import yaml

_DIR = Path(__file__).parent


def load_profile(language: str, variant: str) -> dict:
    path = _DIR / f"{language}_{variant}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))
