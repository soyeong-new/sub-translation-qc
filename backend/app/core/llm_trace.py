"""개발용: 모델 호출의 입력(시스템·사용자 프롬프트)과 응답 원문을 분석 실행(target_version_id)별
JSONL로 남긴다. 같은 입력에서 응답이 왜 달랐는지, 몇 줄을 돌려줬는지를 나중에 직접 읽기 위한
것이다. media는 git 제외이고 DB와 사용자 화면에는 아무 영향이 없다. 기록 실패가 분석을 깨지
않도록 모든 예외를 삼킨다. 파이프라인 밖 호출(trace_id 없음)은 남기지 않는다. API 키와 요청
헤더는 다루지 않는다 — 프롬프트와 응답 텍스트만 쓴다. 테스트는 conftest가 TRACE_DIR를 None으로 끈다."""

import contextvars
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TRACE_DIR: Optional[Path] = Path(__file__).resolve().parents[2] / "media" / "qc_trace"

# 분석 시작 지점(background.py)이 실행 id를 넣어 둔다. asyncio 태스크마다 복사되므로
# 동시에 도는 다른 실행의 기록과 섞이지 않는다.
trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("llm_trace_id", default="")


def record(provider: str, label: str, model: str, system: str, user: str, output: str,
           usage: Optional[dict] = None) -> None:
    run_id = trace_id.get()
    if TRACE_DIR is None or not run_id:
        return
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": provider, "label": label,
               "model": model, "system": system, "user": user, "output": output, "usage": usage}
        with open(TRACE_DIR / f"{run_id}.llm.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.debug("모델 입출력 기록 실패(무시)", exc_info=True)
