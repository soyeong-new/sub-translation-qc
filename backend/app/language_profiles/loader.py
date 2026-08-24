"""언어별(성별 일치·격식 체계) 설정을 YAML에서 안전하게 불러오는 모듈."""

from pathlib import Path
import re
import yaml

_DIR = Path(__file__).parent


def load_profile(language: str, variant: str) -> dict:
    # Input validation: only allow alphabetic characters to prevent path traversal
    if not re.fullmatch(r"[a-zA-Z]+", language):
        raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: 유효하지 않은 언어 코드: {language}")
    if not re.fullmatch(r"[a-zA-Z]+", variant):
        raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: 유효하지 않은 변형 코드: {variant}")

    path = _DIR / f"{language}_{variant}.yaml"

    # Defense in depth: verify resolved path is still within _DIR
    try:
        if not path.resolve().is_relative_to(_DIR.resolve()):
            raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: {path}")
    except ValueError:
        # is_relative_to raises ValueError on different drives (Windows edge case)
        raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: {path}")

    if not path.exists():
        raise FileNotFoundError(f"언어 프로파일을 찾을 수 없습니다: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def list_profiles() -> list[dict]:
    """language_profiles/ 폴더의 {language}_{variant}.yaml 파일을 스캔해
    선택 가능한 언어/변형 목록을 반환한다. 새 프로파일 파일을 추가하면
    코드 수정 없이 이 목록에 자동으로 나타난다."""
    profiles = []
    for path in sorted(_DIR.glob("*.yaml")):
        stem = path.stem  # 예: "es_LATAM"
        if "_" not in stem:
            continue
        language, variant = stem.split("_", 1)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        display_name = data.get("display_name", f"{language} ({variant})")
        profiles.append({"language": language, "variant": variant, "display_name": display_name})
    return profiles
