// 분석(S1) 직후, AI 검증(S2) 전에 반드시 거쳐야 하는 성별/격식 확인 페이지.
// design §AI 검증은 확정된 값을 받고 시작해야 함 — 여기서 다 확인되기 전에는
// findings 화면으로 못 넘어간다(리뷰 화면 안에 파묻힌 선택적 링크가 아니라
// 독립된 필수 단계).

import { useEffect, useRef, useState } from "react";
import {
  getFlaggedSegments, getTargetVersion, resolveGender, resolveGenderGroup,
  resolveFormality, confirmRegisters,
} from "../api.js";
import FlaggedSegmentStepper, { isSegmentResolved } from "./FlaggedSegmentStepper.jsx";

export default function RegisterConfirmationView({ targetVersionId, onDone, onExit }) {
  const [segments, setSegments] = useState(null); // null = 로딩 중
  const [videoProxyUrl, setVideoProxyUrl] = useState(null);
  // 영상을 잘라 올려 한국어 STT와 대상언어 SRT 사이 상수 오프셋이 감지된
  // 경우(app/core/alignment.py의 detect_global_offset) — segment.start/end는
  // SRT 시계 그대로라 영상 seek 시 이 값을 빼서 영상 파일 자체의 시계로
  // 변환해야 정확한 장면이 나온다.
  const [videoOffsetSeconds, setVideoOffsetSeconds] = useState(0);
  const [error, setError] = useState(null);
  const [completePending, setCompletePending] = useState(false);
  // 확인을 다 마쳤는데(성별/격식 답변 완료) "AI 검증 시작하기" 버튼을 그
  // 자리에서 안 누르고 나가면, target_version이 "awaiting_confirmation"
  // 상태로 남아 이 화면에 "누르면 실제 검증이 도는" 버튼만 덩그러니 걸려
  // 있게 된다 — 나중에 "열기"로 다시 들어왔을 때 이게 "예전 결과를 보여줄
  // 뿐"인 버튼처럼 보여 무심코 누르면 진짜 새 AI 검증(과 그 API 비용)이
  // 나가는 문제가 있었다(사용자 피드백). segments가 빈 배열이 되는 순간
  // (마지막 질문에 막 답했을 때든, 로딩 시점에 이미 다 확인돼 있었든) 자동
  // 으로 검증을 시작해, "확인은 끝났지만 검증 안 함"이라는 애매한 상태 자체가
  // 생기지 않게 한다. autoStartedRef로 한 번만 자동 실행되게 막는다 —
  // 실패하면(completePending이 다시 false로 풀림) 스테퍼의 버튼으로
  // 수동 재시도할 수 있다.
  const autoStartedRef = useRef(false);

  useEffect(() => {
    if (segments !== null && segments.length === 0 && !autoStartedRef.current) {
      autoStartedRef.current = true;
      handleComplete();
    }
  }, [segments]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getFlaggedSegments(targetVersionId), getTargetVersion(targetVersionId)])
      .then(([flagged, tv]) => {
        if (cancelled) return;
        // 로딩 시점에 이미 자동으로(한국어 어미/호칭 등으로) 확인 완료된
        // 줄은 애초에 목록에 넣지 않는다 — 사람이 답할 필요가 전혀 없었던
        // 것까지 총 건수에 들어가면 "1/50" 처럼 실제 확인할 개수를 부풀려
        // 보이게 한다. 여기서 한 번만 걸러내고, 이후 사람이 직접 답한
        // 항목은(handleResolveGender/Formality가 배열 안에서 갱신만 하고
        // 다시 거르지 않으므로) 계속 남아 있어 되돌아가 수정할 수 있다.
        setSegments(flagged.filter((s) => !isSegmentResolved(s)));
        setVideoProxyUrl(tv.video_proxy_url ?? null);
        setVideoOffsetSeconds(tv.video_offset_seconds ?? 0);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? "확인할 줄을 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [targetVersionId]);

  async function handleComplete() {
    setCompletePending(true);
    setError(null);
    try {
      await confirmRegisters(targetVersionId);
      await pollUntilReview();
      onDone();
    } catch (err) {
      setError(err.message ?? "AI 검증 시작 중 오류가 발생했습니다.");
      setCompletePending(false);
    }
  }

  function pollUntilReview() {
    return new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const tv = await getTargetVersion(targetVersionId);
          if (tv.status === "review") {
            resolve();
          } else if (tv.status === "failed") {
            reject(new Error(tv.error_message || "AI 검증 중 오류가 발생했습니다."));
          } else {
            setTimeout(poll, 2000);
          }
        } catch (err) {
          reject(err);
        }
      };
      poll();
    });
  }

  async function handleResolveGender(segmentId, gender) {
    const updated = await resolveGender(segmentId, gender);
    setSegments((prev) => prev.map((s) => (s.id === segmentId ? { ...s, ...updated } : s)));
    return updated;
  }

  async function handleResolveGenderGroup(segmentId, groupIndex, gender) {
    const updated = await resolveGenderGroup(segmentId, groupIndex, gender);
    setSegments((prev) => prev.map((s) => (s.id === segmentId ? { ...s, ...updated } : s)));
    return updated;
  }

  async function handleResolveFormality(segmentId, formalityLevel) {
    const updated = await resolveFormality(segmentId, formalityLevel);
    setSegments((prev) => prev.map((s) => (s.id === segmentId ? { ...s, ...updated } : s)));
    return updated;
  }

  if (segments === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      </div>
    );
  }

  if (completePending) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">AI 검증 진행 중... (시간이 걸릴 수 있습니다)</p>
      </div>
    );
  }

  return (
    <>
      <FlaggedSegmentStepper
        segments={segments}
        videoProxyUrl={videoProxyUrl}
        videoOffsetSeconds={videoOffsetSeconds}
        onResolveGender={handleResolveGender}
        onResolveGenderGroup={handleResolveGenderGroup}
        onResolveFormality={handleResolveFormality}
        onComplete={handleComplete}
        completePending={completePending}
        onExit={onExit}
      />
      {error && (
        <p role="status" aria-live="polite"
          className="fixed bottom-4 left-1/2 z-[60] -translate-x-1/2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}
