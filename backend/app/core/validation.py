"""업로드된 파일이 실제로 MEDIA_ROOT의 예상 서브디렉터리 안을 가리키는지 검증한다."""

from pathlib import Path
from fastapi import HTTPException
from app.core.uploads import MEDIA_ROOT


def validate_media_subpath(path: str, subdir: str, error_message: str) -> None:
    """path가 실제로 MEDIA_ROOT/subdir 아래를 가리키는지 확인한다 — is_relative_to는
    lexical하게만 비교하므로 ".."이 섞인 경로를 resolve() 없이 검사하면 실제로는
    밖을 가리키는 경로(예: /etc/passwd)도 통과시킬 수 있다. 클라이언트가 임의의
    경로를 넘겨 서버가 그 파일을 열어(추출/파싱 후 API 응답으로 그대로 서빙하거나
    Anthropic API로 전송) 임의 파일 읽기 통로가 되는 것을 막는다."""
    target_dir = MEDIA_ROOT / subdir
    try:
        resolved_path = Path(path).resolve()
        resolved_dir = target_dir.resolve()
        if not resolved_path.is_relative_to(resolved_dir):
            raise HTTPException(400, error_message)
    except ValueError:
        # 다른 드라이브(Windows) 등 방어적 예외 상황도 무효 처리한다.
        raise HTTPException(400, error_message)


def validate_chart_image_path(image_path: str) -> None:
    """image_path가 실제로 MEDIA_ROOT/chart_image 아래를 가리키는지 확인한다."""
    validate_media_subpath(image_path, "chart_image", "유효하지 않은 이미지 경로입니다.")


def validate_english_srt_path(english_srt_path: str) -> None:
    """english_srt_path가 실제로 MEDIA_ROOT/srt_en 아래를 가리키는지 확인한다 —
    pipeline.load_srt()가 이 경로를 열어 파싱하고, 매칭된 텍스트가
    Segment.english_pronoun_hint로 저장되어 flagged-segments 응답을 통해 그대로
    클라이언트에 노출되므로 chart_image_path와 동일한 이유로 검증이 필요하다."""
    validate_media_subpath(english_srt_path, "srt_en", "유효하지 않은 영어 자막 경로입니다.")
