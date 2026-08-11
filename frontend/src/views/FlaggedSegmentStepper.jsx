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

// 성별 표시가 문장 속 누구를 가리키는지(1인칭=화자 자신/2인칭=대화 상대/
// 3인칭=제3자) — spaCy 인칭 태그를 그대로 안내 문구로 옮긴 것. "지금 묻는
// 성별이 화자 본인 건지, 화자가 얘기하는 다른 사람 건지" 헷갈린다는 피드백이
// 있어 괄호로 풀어서 설명을 덧붙인다.
export const PERSON_LABELS = {
  "1": "화자 자신 (지금 말하는 사람)",
  "2": "대화 상대 (지금 말을 듣는 사람)",
  "3": "제3자 (대화에 없는 다른 인물)",
};

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
// 쓰이고(기존 동작과 동일), 인물이 둘 이상이면(그룹) 인물 수만큼 반복해서
// 쓰인다. personLabel이 있으면(단일 인물 줄, 인칭 정보가 있을 때) 위에
// 안내 문구를 보여주고, 없으면(다인물 줄) 생략한다 — 다인물 줄은 대신
// heading에 몇 번째 인물인지가 이미 표시된다.
export function GenderQuestion({
  heading, words, wordMeanings, personLabel, resolvedGender,
  suggestedNotApplicable, hint, pending, onSelect,
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
      {/* "이게 화자 본인 성별이냐, 화자가 얘기하는 다른 사람 성별이냐"가
          헷갈린다는 피드백 — 버튼 누르기 전에 누구 얘기인지부터 분명하게
          밝힌다(버튼 아래 잔글씨로만 있던 걸 위로 올림). 다인물 줄은 인칭
          만으로 인물을 구분할 수 없어(문법적으로는 같은 3인칭인 경우가
          흔함) 대신 heading으로 몇 번째 인물인지 이미 밝혔으므로 생략한다. */}
      {personLabel && (
        <p className="mb-3 rounded-md bg-accent/10 px-3 py-2 text-sm text-foreground">
          이 성별은 <strong>{personLabel}</strong>의 성별입니다.
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
            검수자가 판단한다. 이 단어가 다른 프로젝트에서도 "해당 없음"으로
            만 판정된 이력이 있으면(한 번도 실제 성별로 판정된 적 없으면)
            추천 표시를 한다 — 그래도 질문 자체는 그대로 뜬다, 숨기지
            않는다(design §반증 사례를 잡을 창구는 항상 열어둠). */}
        <button
          disabled={pending}
          onClick={() => onSelect("not_applicable")}
          className={
            resolvedGender === "not_applicable"
              ? selectedBtnClass
              : suggestedNotApplicable
                ? `${btnBase} border border-accent bg-accent/20 text-accent-foreground hover:bg-accent/30`
                : binaryBtnClass
          }
        >
          해당 없음(사람 아님){suggestedNotApplicable && " · 추천"}
        </button>
      </div>
      {hint?.text && (
        <p className="mt-3 text-xs text-muted-foreground">
          영어 자막 힌트: he {hint.he_count} · she {hint.she_count}
          <br />
          &ldquo;{hint.text}&rdquo;
        </p>
      )}
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
    // 구간 끝에서 딱 한 번만 멈춘다 — pausedAtEnd 없이 매 timeupdate마다
    // currentTime >= end를 계속 검사하면, 검수자가 멈춘 뒤 controls로 다시
    // 재생을 눌러도 currentTime이 여전히 end 이상이라 즉시 재차 멈춰버려서
    // "재생이 안 된다"로 보이는 버그가 있었다.
    let pausedAtEnd = false;
    function handleTimeUpdate() {
      if (!pausedAtEnd && video.currentTime >= seekEnd) {
        pausedAtEnd = true;
        video.pause();
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
    return () => video.removeEventListener("timeupdate", handleTimeUpdate);
  }, [currentSegment?.id, currentSegment?.start, currentSegment?.end, videoOffsetSeconds]);

  function goToNextUnresolved(fromIndex) {
    for (let i = fromIndex + 1; i < segments.length; i += 1) {
      if (!isSegmentResolved(segments[i])) {
        setCurrentIndex(i);
        return;
      }
    }
  }

  async function handleResolveGender(gender) {
    if (!currentSegment) return;
    const wasResolved = isSegmentResolved(currentSegment);
    setPending(true);
    setError(null);
    try {
      const updated = await onResolveGender(currentSegment.id, gender);
      // 처음 답하는 경우에만 다음 미확인 줄로 자동 이동한다 — 이미 확인된
      // 줄을 되돌아가 수정한 경우(wasResolved)는 검수자가 일부러 그 줄로
      // 온 것이므로 제자리에 그대로 둔다.
      if (!wasResolved && isSegmentResolved({ ...currentSegment, ...updated })) {
        goToNextUnresolved(currentIndex);
      }
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setPending(false);
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
  const hint = currentSegment.english_pronoun_hint;
  const genderGroups = currentSegment.resolved_gender_groups_raw;
  const hasMultipleReferents = genderGroups && genderGroups.length > 0;

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
              {hasMultipleReferents ? (
                // 한 줄에 성별이 다른 인물이 둘 이상 있으면, 확정된 성별
                // 하나를 문장 전체에 뭉뚱그려 적용할 수 없다(엉뚱한 인물의
                // 단어까지 잘못 바뀜) — 그래서 인물별로 질문을 따로 나눠
                // 보여주고 각각 답을 받는다.
                <div className="space-y-4">
                  <p className="text-xs text-muted-foreground">
                    이 줄엔 성별이 다른 인물이 {genderGroups.length}명 있습니다 — 각각 따로 확인해주세요.
                  </p>
                  {genderGroups.map((group, index) => (
                    <div key={index} className="rounded-md border border-border/60 p-3">
                      <GenderQuestion
                        heading={group.referent ? `인물 ${index + 1} (${group.referent})` : `인물 ${index + 1}`}
                        words={group.words}
                        wordMeanings={group.word_meanings}
                        personLabel={
                          group.person
                            ? PERSON_LABELS[group.person] ?? "누구인지 확인 필요(영상을 보고 판단)"
                            : null
                        }
                        resolvedGender={group.gender}
                        suggestedNotApplicable={group.suggested_not_applicable}
                        hint={null}
                        pending={pending}
                        onSelect={(gender) => handleResolveGenderGroup(index, gender)}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <GenderQuestion
                  words={hint?.target_words}
                  wordMeanings={hint?.word_meanings}
                  personLabel={PERSON_LABELS[hint?.grammatical_person] ?? "누구인지 확인 필요(영상을 보고 판단)"}
                  resolvedGender={currentSegment.resolved_gender_raw}
                  suggestedNotApplicable={hint?.suggested_not_applicable}
                  hint={hint}
                  pending={pending}
                  onSelect={handleResolveGender}
                />
              )}
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
