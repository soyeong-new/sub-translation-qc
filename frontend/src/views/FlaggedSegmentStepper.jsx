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
// 지금 저장된 답과 같은 버튼임을 보여주는 스타일 — 다른 버튼을 누르면
// 그대로 덮어써서 수정된다(별도 "수정" 모드 없이 그냥 다시 클릭하면 됨).
const selectedBtnClass = `${btnBase} border border-primary bg-primary/15 text-primary`;
const navBtnClass =
  `${btnBase} border border-input bg-background text-foreground hover:bg-accent hover:text-accent-foreground`;
const primaryBtnClass =
  `${btnBase} bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2`;
const ghostBtnClass =
  `${btnBase} text-muted-foreground hover:bg-accent hover:text-accent-foreground`;

// 성별/격식 각각의 확인 상태를 판단하는 단일 기준점. isSegmentResolved와
// 컴포넌트 렌더 로직(genderResolved/formalityResolved) 모두 이 두 함수를
// 그대로 재사용해, "확인됨"의 정의가 여러 곳에서 어긋나지 않게 한다.
// 한 줄에 인물이 둘 이상이면(resolved_gender_groups_raw) 그룹 전부가
// 답변돼야 확인된 것이다 — 하나라도 비어 있으면 아직 미확인이다.
export function isGenderResolved(segment) {
  if (!segment.gender_check_needed) return true;
  const groups = segment.resolved_gender_groups_raw;
  if (groups && groups.length > 0) {
    return groups.every((g) => Boolean(g.gender));
  }
  return Boolean(segment.resolved_gender_raw);
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

// 성별 확인 질문 하나를 그린다 — 인물이 하나뿐인 줄은 이 컴포넌트가 한 번만
// 쓰이고, 인물이 둘 이상이면(그룹) 인물 수만큼 반복해서 쓰인다. referent가
// 있으면(LLM이 판단한, 이 그룹이 누구인지에 대한 짧은 설명) 위에 안내
// 문구를 보여주고, 없으면 생략한다.
export function GenderQuestion({
  heading, words, wordMeanings, referent, resolvedGender, pending, onSelect,
}) {
  return (
    <div>
      {heading && <p className="mb-2 text-xs font-semibold text-muted-foreground">{heading}</p>}
      {words?.length > 0 && (
        <p className="mb-3 text-sm text-foreground">
          성수 구분이 필요한 표현:{" "}
          {words.map((w) => (
            <span key={w} className="mr-1.5 whitespace-nowrap">
              <span className="rounded bg-accent/20 px-1.5 py-0.5 font-mono text-accent-foreground">
                {w}
              </span>
              {wordMeanings?.[w] && (
                <span className="ml-1 text-muted-foreground">({wordMeanings[w]})</span>
              )}
            </span>
          ))}
        </p>
      )}
      {referent && (
        <p className="mb-3 rounded-md bg-accent/10 px-3 py-2 text-sm text-foreground">
          이 성별은 <strong>{referent}</strong>의 성별입니다.
        </p>
      )}
      {/* 이미 확인된 줄이어도 버튼은 계속 눌러서 답을 바꿀 수 있다 — 지금
          저장된 값과 같은 버튼만 강조 표시한다. */}
      <div className="flex flex-wrap gap-2">
        <button
          disabled={pending}
          onClick={() => onSelect("male")}
          className={resolvedGender === "male" ? selectedBtnClass : binaryBtnClass}
        >
          남성
        </button>
        <button
          disabled={pending}
          onClick={() => onSelect("female")}
          className={resolvedGender === "female" ? selectedBtnClass : binaryBtnClass}
        >
          여성
        </button>
        {/* caro(비싸다)처럼 성별 표시가 걸렸지만 실제로는 사람이 아니라
            사물/상황을 가리키는 단어일 때 쓰는 탈출구 — 뜻풀이를 보고
            검수자가 판단한다. */}
        <button
          disabled={pending}
          onClick={() => onSelect("not_applicable")}
          className={resolvedGender === "not_applicable" ? selectedBtnClass : binaryBtnClass}
        >
          해당 없음(사람 아님)
        </button>
      </div>
    </div>
  );
}

// segments prop은 확인이 필요했던 줄 전체를 담는다(이미 확인된 줄도 포함) —
// 검수자가 "이전 줄"로 돌아가 이미 답한 것도 다시 볼 수 있고, 버튼을 다시
// 눌러 답을 바꿀 수도 있어야 하기 때문이다(바꾸면 최신 답으로 그대로
// 덮어써서 저장된다).
export default function FlaggedSegmentStepper({
  segments, videoProxyUrl, videoOffsetSeconds = 0,
  onResolveGender, onResolveGenderGroup, onResolveFormality, onComplete, completePending, onExit,
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
  const allResolved = unresolvedCount === 0;

  // 줄이 바뀔 때마다 그 줄의 구간으로 seek하고 재생하며, 구간 끝에 도달하면
  // 멈춘다(반복재생 아님 — 필요하면 검수자가 <video controls>로 직접 다시
  // 재생). <video> 엘리먼트 자체는 이 컴포넌트가 살아있는 동안 절대 바뀌지
  // 않는다 — 그래서 리로드 없이 seek만 일어난다.
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
    // segment.start/end는 대상언어 SRT 시계다 — 영상을 잘라 올려 SRT와
    // 영상 파일 시계가 어긋나 있으면(detect_global_offset이 감지),
    // videoOffsetSeconds를 빼서 영상 파일 자체의 시계로 변환해야 실제
    // 그 대사가 나오는 장면으로 seek된다. 오프셋이 없으면(0) 그대로다.
    const seekStart = currentSegment.start - videoOffsetSeconds;
    const seekEnd = currentSegment.end - videoOffsetSeconds;
    // 구간 끝에 도달하면 멈춘다. 회귀(사용자 재현): 예전엔 "한 번만 멈춘다"는
    // 플래그(pausedAtEnd)로 이 재정지를 막았는데, 그 결과 검수자가 자동정지
    // 후 재생 버튼을 다시 누르면 그 플래그가 계속 true로 남아 있어 구간
    // 경계를 완전히 무시하고 영상이 끝까지 흘러가 버렸다. "재생하면 그
    // 구간만 재생된다"는 기대에 맞게, 구간 끝(또는 그 이후)에서 재생이
    // 시작되면 항상 구간 처음으로 되감아 다시 그 구간만 재생한다 — 구간
    // 도중에 잠깐 멈췄다 이어보는 정상적인 일시정지/재개는 되감지 않는다.
    function handleTimeUpdate() {
      if (video.currentTime >= seekEnd) {
        video.pause();
      }
    }
    function handlePlay() {
      if (video.currentTime >= seekEnd) {
        video.currentTime = seekStart;
      }
    }
    video.currentTime = seekStart;
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => {
        // 음소거가 아니라서 브라우저 자동재생 정책으로 재생이 거부될 수
        // 있다 — <video controls>가 있으므로 검수자가 직접 재생 버튼을
        // 누를 수 있어, 여기서는 조용히 무시한다.
      });
    }
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("play", handlePlay);
    return () => {
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.removeEventListener("play", handlePlay);
    };
  }, [currentSegment?.id, currentSegment?.start, currentSegment?.end, videoOffsetSeconds]);

  function goToNextUnresolved(fromIndex) {
    for (let i = fromIndex + 1; i < segments.length; i += 1) {
      if (!isSegmentResolved(segments[i])) {
        setCurrentIndex(i);
        return;
      }
    }
  }

  async function handleResolveGenderGroup(groupIndex, gender) {
    if (!currentSegment) return;
    const wasResolved = isSegmentResolved(currentSegment);
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveGenderGroup(currentSegment.id, groupIndex, gender);
      if (!wasResolved && isSegmentResolved({ ...currentSegment, ...updated })) {
        goToNextUnresolved(currentIndex);
      }
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setPending(false);
    }
  }

  // 한국어 규칙(_detect_korean_gender)이 후보 하나뿐인 줄을 자동으로 이미
  // 확정한 경우, 그 값은 그룹이 아니라 resolved_gender_raw 단일값에
  // 들어간다(pipeline._run_grammar_necessity_check) — genderGroups가
  // 비어 있는 게 정상이다. ReviewView.jsx의 InlineGenderQuestion과 같은
  // fallback이 여기도 필요하다: 그룹이 없을 때만 쓰는 단일값 경로.
  async function handleResolveGender(gender) {
    if (!currentSegment) return;
    const wasResolved = isSegmentResolved(currentSegment);
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveGender(currentSegment.id, gender);
      if (!wasResolved && isSegmentResolved({ ...currentSegment, ...updated })) {
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
    const wasResolved = isSegmentResolved(currentSegment);
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveFormality(currentSegment.id, formalityLevel);
      if (!wasResolved && isSegmentResolved({ ...currentSegment, ...updated })) {
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
          <div className="mt-4 flex justify-center gap-2">
            {onExit && (
              <button onClick={onExit} className={ghostBtnClass}>목록으로</button>
            )}
            <button disabled={completePending} onClick={onComplete} className={primaryBtnClass}>
              AI 검증 시작하기
            </button>
          </div>
        </div>
      </div>
    );
  }

  const genderResolved = isGenderResolved(currentSegment);
  const formalityResolved = isFormalityResolved(currentSegment);
  const genderGroups = currentSegment.resolved_gender_groups_raw || [];

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
        <div className="flex gap-2">
          {onExit && (
            <button onClick={onExit} className={ghostBtnClass}>목록으로</button>
          )}
          {allResolved && (
            <button disabled={completePending} onClick={onComplete} className={primaryBtnClass}>
              AI 검증 시작하기
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6 lg:flex-row">
        <div className="lg:w-1/2">
          {videoProxyUrl ? (
            <video
              ref={videoRef}
              src={videoProxyUrl}
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
              <div className="mb-3 flex items-center gap-2">
                <h3 id="stepper-gender-heading" className="text-sm font-semibold text-foreground">성별</h3>
                {genderResolved && <span className="text-xs text-success">확인됨</span>}
              </div>
              <div className="space-y-4">
                {genderGroups.length > 1 && (
                  <p className="text-xs text-muted-foreground">
                    이 줄엔 성별이 다른 인물이 {genderGroups.length}명 있습니다 — 각각 따로 확인해주세요.
                  </p>
                )}
                {genderGroups.length > 0 ? (
                  genderGroups.map((group, index) => (
                    <div key={index} className={genderGroups.length > 1 ? "rounded-md border border-border/60 p-3" : ""}>
                      <GenderQuestion
                        heading={
                          genderGroups.length > 1
                            ? (group.referent ? `인물 ${index + 1} (${group.referent})` : `인물 ${index + 1}`)
                            : null
                        }
                        words={group.words}
                        wordMeanings={group.word_meanings}
                        referent={genderGroups.length > 1 ? null : group.referent}
                        resolvedGender={group.gender}
                        pending={pending}
                        onSelect={(gender) => handleResolveGenderGroup(index, gender)}
                      />
                    </div>
                  ))
                ) : (
                  <GenderQuestion
                    resolvedGender={currentSegment.resolved_gender_raw}
                    pending={pending}
                    onSelect={(gender) => handleResolveGender(gender)}
                  />
                )}
              </div>
            </section>
          )}

          {currentSegment.formality_check_needed && (
            <section aria-labelledby="stepper-formality-heading" className="rounded-lg border border-border bg-card p-4">
              <div className="mb-3 flex items-center gap-2">
                <h3 id="stepper-formality-heading" className="text-sm font-semibold text-foreground">격식</h3>
                {formalityResolved && <span className="text-xs text-success">확인됨</span>}
              </div>
              <div className="flex gap-2">
                <button
                  disabled={pending}
                  onClick={() => handleResolveFormality("formal")}
                  className={currentSegment.resolved_formality_raw === "formal" ? selectedBtnClass : binaryBtnClass}
                >
                  존댓말
                </button>
                <button
                  disabled={pending}
                  onClick={() => handleResolveFormality("informal")}
                  className={currentSegment.resolved_formality_raw === "informal" ? selectedBtnClass : binaryBtnClass}
                >
                  반말
                </button>
              </div>
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
