// 영상과 자막 싱크를 검수자가 직접 맞추는 버튼 — 영상 한 편에 값 하나(video_offset_seconds)를
// 저장하므로 모든 줄에 똑같이 적용된다(줄마다 다른 어긋남은 못 맞춘다).
// 재생 위치는 `줄 시작 - 오프셋`이라, 영상의 지금 위치가 줄 시작이 되도록 오프셋을 다시 계산한다.

import { useState } from "react";
import { btnBase } from "../utils/buttonStyles.js";

const syncBtnClass =
  `${btnBase} bg-primary px-2.5 py-1 text-xs text-primary-foreground hover:bg-primary/90`;

export default function VideoSyncControl({ segmentStart, videoRef, onChange }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  async function alignToCurrentTime() {
    const video = videoRef?.current;
    if (!video || segmentStart == null) return;
    setPending(true);
    setError(null);
    try {
      await onChange(Math.round((segmentStart - video.currentTime) * 100) / 100);
    } catch (err) {
      setError(err.message ?? "싱크를 저장하지 못했습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div data-tour="video-sync" className="relative">
      <button
        disabled={pending || segmentStart == null}
        onClick={alignToCurrentTime}
        title={
          segmentStart == null
            ? "자막이나 카드를 눌러 줄을 먼저 재생해 주세요."
            : "영상에서 말이 시작되는 곳에 멈추고 누르세요. 이 영상의 모든 줄에 같은 만큼 적용됩니다."
        }
        className={syncBtnClass}
      >
        싱크 맞추기
      </button>
      {error && (
        <p
          role="status"
          aria-live="polite"
          className="absolute left-1/2 top-full z-10 mt-1.5 w-max max-w-[16rem] -translate-x-1/2 rounded-md border border-destructive/40 bg-card px-3 py-2 text-sm font-medium text-destructive shadow-md"
        >
          {error}
        </p>
      )}
    </div>
  );
}
