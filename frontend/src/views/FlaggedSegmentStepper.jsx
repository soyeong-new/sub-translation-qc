// 성별/격식 확인이 필요한 줄을 한 번에 하나씩 보여주는 풀스크린 리뷰 스텝퍼.
// design §사람 리뷰 UI: "한 번에 하나씩 보여주는 풀스크린 스텝퍼. 그 줄의
// 시점 영상 클립이 자동재생되고, 대사 텍스트가 함께 표시된다." 같은 씬의
// 줄 사이를 이동할 때 <video>를 다시 로드하면 안 되므로(design §같은 씬에
// 걸린 줄이 여러 개면 영상을 다시 로드하지 않는다), <video>는 이 컴포넌트가
// 마운트되는 동안 단 한 번만 만들어지고 줄이 바뀔 때는 currentTime만
// 이동한다 — React key로 강제 리마운트하지 않는다.

import { useEffect, useRef, useState } from "react";

const btnBase =
  "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";

const binaryBtnClass =
  `${btnBase} border border-input bg-background text-foreground hover:bg-accent hover:text-accent-foreground`;
const navBtnClass =
  `${btnBase} border border-input bg-background text-foreground hover:bg-accent hover:text-accent-foreground`;
const closeBtnClass =
  `${btnBase} text-muted-foreground hover:bg-accent hover:text-accent-foreground`;

// 성별 표시가 문장 속 누구를 가리키는지(1인칭=화자 자신/2인칭=대화 상대/
// 3인칭=제3자) — spaCy 인칭 태그를 그대로 안내 문구로 옮긴 것.
const PERSON_LABELS = { "1": "화자 자신", "2": "대화 상대", "3": "제3자(다른 인물)" };

// 성별/격식 각각의 확인 상태를 판단하는 단일 기준점. isSegmentResolved와
// 컴포넌트 렌더 로직(genderResolved/formalityResolved) 모두 이 두 함수를
// 그대로 재사용해, "확인됨"의 정의가 여러 곳에서 어긋나지 않게 한다.
function isGenderResolved(segment) {
  return !segment.gender_check_needed || Boolean(segment.resolved_gender_raw);
}

function isFormalityResolved(segment) {
  return !segment.formality_check_needed || Boolean(segment.resolved_formality_raw);
}

// Segment의 확인 상태를 판단하는 단일 기준점. ReviewView.jsx가 진입 버튼의
// 미확인 개수 배지를 계산할 때도 이 함수를 그대로 재사용해, "확인됨"의
// 정의가 두 곳에서 어긋나지 않게 한다.
export function isSegmentResolved(segment) {
  return isGenderResolved(segment) && isFormalityResolved(segment);
}

export default function FlaggedSegmentStepper({
  segments, videoProxyUrl, onResolveGender, onResolveFormality, onClose,
}) {
  const [currentIndex, setCurrentIndex] = useState(() => {
    const idx = segments.findIndex((s) => !isSegmentResolved(s));
    return idx === -1 ? 0 : idx;
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const videoRef = useRef(null);

  const currentSegment = segments[currentIndex] ?? null;
  const unresolvedCount = segments.filter((s) => !isSegmentResolved(s)).length;

  // 줄이 바뀔 때마다 그 줄의 구간으로 seek하고 재생한다. 구간 끝에 도달하면
  // 처음으로 되돌려 반복 재생한다(자동재생 요구사항 + "여러 번 다시 보기"를
  // 매번 수동 되감기 없이 지원). <video> 엘리먼트 자체는 이 컴포넌트가 살아있는
  // 동안 절대 바뀌지 않는다 — 그래서 리로드 없이 seek만 일어난다.
  //
  // 의존성 배열은 일부러 currentSegment 전체가 아니라 id/start/end 세
  // 프리미티브만 담는다 — 성별·격식 중 하나만 해결됐을 때(둘 다 필요한
  // 줄에서), ReviewView가 세그먼트 배열을 { ...s, ...updated } 스프레드로
  // 새 객체 참조를 만들어 넘겨줘도 같은 줄(id/start/end 불변)이면 이 이펙트가
  // 재실행되지 않는다. currentSegment 객체 전체를 의존성으로 두면 매 부분
  // 해결마다 새 객체로 인식돼 아직 검수 중인 영상이 처음으로 되감겨 재생되는
  // 문제가 있었다(Finding #2).
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !currentSegment) return undefined;
    function handleTimeUpdate() {
      if (video.currentTime >= currentSegment.end) {
        video.currentTime = currentSegment.start;
      }
    }
    video.currentTime = currentSegment.start;
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => {
        // 브라우저 자동재생 정책으로 재생이 거부될 수 있다 — <video controls>가
        // 있으므로 검수자가 직접 재생 버튼을 누를 수 있어, 여기서는 조용히
        // 무시한다.
      });
    }
    video.addEventListener("timeupdate", handleTimeUpdate);
    return () => video.removeEventListener("timeupdate", handleTimeUpdate);
  }, [currentSegment?.id, currentSegment?.start, currentSegment?.end]);

  function goToNextUnresolved(fromIndex) {
    for (let i = fromIndex + 1; i < segments.length; i += 1) {
      if (!isSegmentResolved(segments[i])) {
        setCurrentIndex(i);
        return;
      }
    }
    for (let i = 0; i < segments.length; i += 1) {
      if (!isSegmentResolved(segments[i])) {
        setCurrentIndex(i);
        return;
      }
    }
  }

  async function handleResolveGender(gender) {
    if (!currentSegment) return;
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveGender(currentSegment.id, gender);
      if (isSegmentResolved({ ...currentSegment, ...updated })) {
        goToNextUnresolved(currentIndex);
      }
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setPending(false);
    }
  }

  async function handleResolveFormality(formalityLevel) {
    if (!currentSegment) return;
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveFormality(currentSegment.id, formalityLevel);
      if (isSegmentResolved({ ...currentSegment, ...updated })) {
        goToNextUnresolved(currentIndex);
      }
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setPending(false);
    }
  }

  if (!currentSegment) {
    return (
      <div role="dialog" aria-modal="true"
        className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 p-6">
        <div className="max-w-sm rounded-lg border border-border bg-card p-6 text-center shadow-lg">
          <p className="text-sm text-foreground">확인할 줄이 없습니다.</p>
          <button onClick={onClose} className={`${closeBtnClass} mt-4`}>닫기</button>
        </div>
      </div>
    );
  }

  const genderResolved = isGenderResolved(currentSegment);
  const formalityResolved = isFormalityResolved(currentSegment);
  const hint = currentSegment.english_pronoun_hint;

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="stepper-heading"
      className="fixed inset-0 z-50 flex flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h2 id="stepper-heading" className="text-lg font-semibold text-foreground">
            성별·격식 확인
          </h2>
          <p className="text-sm text-muted-foreground">
            {currentIndex + 1} / {segments.length} · 남은 확인 {unresolvedCount}건
          </p>
        </div>
        <button onClick={onClose} className={closeBtnClass}>닫기</button>
      </header>

      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6 lg:flex-row">
        <div className="lg:w-1/2">
          {videoProxyUrl ? (
            <video
              ref={videoRef}
              src={videoProxyUrl}
              muted
              playsInline
              controls
              className="w-full rounded-lg border border-border bg-black"
            />
          ) : (
            <div className="flex aspect-video items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
              영상 프록시를 사용할 수 없습니다.
            </div>
          )}
          <div className="mt-4 rounded-md border border-border bg-card p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">한국어 원문</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-foreground">{currentSegment.korean_text}</p>
            <p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">대상언어</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-foreground">{currentSegment.target_text}</p>
          </div>
        </div>

        <div className="space-y-6 lg:w-1/2">
          {currentSegment.gender_check_needed && (
            <section aria-labelledby="stepper-gender-heading" className="rounded-lg border border-border bg-card p-4">
              <h3 id="stepper-gender-heading" className="mb-3 text-sm font-semibold text-foreground">성별</h3>
              {genderResolved ? (
                <p className="text-sm text-success">확인됨</p>
              ) : (
                <div className="flex gap-2">
                  <button disabled={pending} onClick={() => handleResolveGender("male")} className={binaryBtnClass}>
                    남성
                  </button>
                  <button disabled={pending} onClick={() => handleResolveGender("female")} className={binaryBtnClass}>
                    여성
                  </button>
                </div>
              )}
              {hint && (hint.grammatical_person || hint.text) && (
                <p className="mt-3 text-xs text-muted-foreground">
                  {hint.grammatical_person && (
                    <>
                      {PERSON_LABELS[hint.grammatical_person] ?? "누구 얘기인지 불명확"}을 가리키는 표현입니다.
                      <br />
                    </>
                  )}
                  {hint.text && (
                    <>
                      영어 자막 힌트: he {hint.he_count} · she {hint.she_count}
                      <br />
                      &ldquo;{hint.text}&rdquo;
                    </>
                  )}
                </p>
              )}
            </section>
          )}

          {currentSegment.formality_check_needed && (
            <section aria-labelledby="stepper-formality-heading" className="rounded-lg border border-border bg-card p-4">
              <h3 id="stepper-formality-heading" className="mb-3 text-sm font-semibold text-foreground">격식</h3>
              {formalityResolved ? (
                <p className="text-sm text-success">확인됨</p>
              ) : (
                <div className="flex gap-2">
                  <button disabled={pending} onClick={() => handleResolveFormality("formal")} className={binaryBtnClass}>
                    존댓말
                  </button>
                  <button disabled={pending} onClick={() => handleResolveFormality("informal")} className={binaryBtnClass}>
                    반말
                  </button>
                </div>
              )}
            </section>
          )}

          {error && (
            <p role="status" aria-live="polite" className="text-sm text-destructive">{error}</p>
          )}
        </div>
      </div>

      <footer className="flex items-center justify-between border-t border-border px-6 py-4">
        <button
          disabled={pending || currentIndex === 0}
          onClick={() => setCurrentIndex((i) => Math.max(0, i - 1))}
          className={navBtnClass}
        >
          &larr; 이전 줄
        </button>
        <button
          disabled={pending || currentIndex === segments.length - 1}
          onClick={() => setCurrentIndex((i) => Math.min(segments.length - 1, i + 1))}
          className={navBtnClass}
        >
          다음 줄 &rarr;
        </button>
      </footer>
    </div>
  );
}
