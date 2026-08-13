// findings 승인/거부/수정, 인물/관계 확인, STT 수정, export를 담당하는 검수 화면.

import { useEffect, useRef, useState } from "react";
import {
  getFindings,
  submitReviewAction,
  requeryFinding,
  pickFinding,
  exportTargetVersion,
  listSegments,
  getTargetVersion,
  correctStt,
  resolveGender,
  resolveGenderGroup,
  excludeSegment,
} from "../api.js";
import { GenderQuestion, isGenderResolved } from "./FlaggedSegmentStepper.jsx";

// 규칙 기반(사전필터, 자동재배치) finding만 재질문 대상이 아니다 — LLM/안전망은
// 전부 가능 (backend/app/core/requery.py의 _LLM_REQUERYABLE_MODELS + "안전망"과
// 대칭). 자동재배치는 줄바꿈만 기계적으로 바꾼 것이라 다시 물어볼 "판단"이 없다.
const NOT_REQUERYABLE_MODELS = ["사전필터", "자동재배치"];
function isRequeryable(finding) {
  return Boolean(finding.model) && !NOT_REQUERYABLE_MODELS.includes(finding.model);
}

// 카테고리 라벨/색상: frontend/tailwind.config.js의 theme.extend.colors.finding.*
// 6종 팔레트를 그대로 재사용한다. 새 색상을 만들지 않는다.
const CATEGORY_LABELS = {
  mistranslation: "오역",
  nuance_tone: "뉘앙스·어조",
  unnatural_style: "직역투",
  locale_convention: "로컬라이제이션",
  sensitivity: "민감어",
  formatting: "포맷팅",
};

const CATEGORY_BADGE_CLASS = {
  mistranslation: "bg-finding-mistranslation-bg text-finding-mistranslation-text border-finding-mistranslation-border",
  nuance_tone: "bg-finding-nuance-tone-bg text-finding-nuance-tone-text border-finding-nuance-tone-border",
  unnatural_style: "bg-finding-unnatural-style-bg text-finding-unnatural-style-text border-finding-unnatural-style-border",
  locale_convention: "bg-finding-locale-convention-bg text-finding-locale-convention-text border-finding-locale-convention-border",
  sensitivity: "bg-finding-sensitivity-bg text-finding-sensitivity-text border-finding-sensitivity-border",
  formatting: "bg-finding-formatting-bg text-finding-formatting-text border-finding-formatting-border",
};
const FALLBACK_BADGE_CLASS = "bg-muted text-muted-foreground border-border";

const STATUS_LABELS = {
  pending: "대기중",
  approved: "승인됨",
  rejected: "거부됨",
  modified: "수정됨",
};

const STATUS_BADGE_CLASS = {
  pending: "bg-muted text-muted-foreground border-border",
  approved: "bg-success/10 text-success border-success/30",
  rejected: "bg-destructive/10 text-destructive border-destructive/30",
  modified: "bg-warning/10 text-warning border-warning/30",
};

const inputClass =
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
  "disabled:cursor-not-allowed disabled:opacity-50";

const labelClass = "mb-1.5 block text-sm font-medium text-foreground";

const btnBase =
  "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";

// 승인/거부/수정 버튼은 색으로만 구분하지 않고(라벨 텍스트 병기) 채움/윤곽 스타일까지
// 다르게 하여 시각적으로 뚜렷이 구분되도록 한다 (ui-ux-pro-max 가이드).
const approveBtnClass = `${btnBase} bg-success text-success-foreground hover:bg-success/90`;
const rejectBtnClass = `${btnBase} bg-destructive text-destructive-foreground hover:bg-destructive/90`;
const modifyBtnClass = `${btnBase} border border-input bg-background text-foreground hover:bg-accent hover:text-accent-foreground`;
const ghostBtnClass = `${btnBase} text-muted-foreground hover:bg-accent hover:text-accent-foreground`;
const primaryBtnClass = `${btnBase} bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2`;

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="h-4 w-4 animate-spin rounded-full border-2 border-current/40 border-t-current"
    />
  );
}

function Field({ id, label, children }) {
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}

// backend/app/core/format_rules.py의 MAX_LINE_CHARS와 동기화되어야 함 —
// 자막은 줄 단위로 글자수 제한이 걸리므로(줄당, 세그먼트 전체가 아님)
// 줄바꿈으로 나눠서 줄마다 따로 세고, 제한을 넘는 줄만 강조 표시한다.
const MAX_LINE_CHARS = 50;

function CharCount({ text }) {
  const lines = text.split("\n");
  return (
    <span className="whitespace-nowrap text-[11px] text-muted-foreground">
      {lines.map((line, i) => (
        <span key={i}>
          {i > 0 && " / "}
          <span className={line.length > MAX_LINE_CHARS ? "font-semibold text-destructive" : ""}>
            {line.length}자
          </span>
        </span>
      ))}
    </span>
  );
}

// description 문자열에서 역번역/재질문 지시문을 떼어낸다 — 단일 카드와
// 짝(paired) 카드의 후보 컬럼이 둘 다 이 로직을 쓴다.
// 역번역은 백엔드가 description 끝에 "(한국어 역번역 참고: ...)"로 붙여
// 보낸다(pipeline.py의 _make_dual_verification_finding). s(dotAll) 플래그가
// 꼭 필요하다 — 대사가 두 줄(예: "- ¿Hablaste con mamá?\n- ¡No!")이면
// 역번역도 줄바꿈이 포함된 여러 줄이 되는데, 기본 "."은 개행 문자를 매칭하지
// 않아 이 경우 정규식 매치 자체가 실패하고 역번역이 description 문단 안에
// 그대로 남아있는 버그가 있었다.
// ponytail: description 포맷 문자열에 결합됨 — 문구가 바뀌면 조용히 안 걸릴
// 뿐 깨지진 않는다(그냥 설명 안에 남아 보임).
function splitDescription(rawDescription) {
  // 원본 역번역은 STT 재검증 경로(findings.py의 correct_stt)가 "(한국어
  // 역번역 참고: ...)" 뒤에 덧붙이므로, 그 태그보다 먼저 떼어내야 한다 —
  // 순서가 바뀌면 뒤쪽 정규식이 앞쪽 태그까지 통째로 삼켜버린다.
  const originalBackTranslationMatch = rawDescription.match(/ \(원본 한국어 역번역 참고: (.+)\)$/s);
  const withoutOriginalBackTranslation = originalBackTranslationMatch
    ? rawDescription.slice(0, originalBackTranslationMatch.index)
    : rawDescription;
  const originalBackTranslation = originalBackTranslationMatch ? originalBackTranslationMatch[1] : null;

  const backTranslationMatch = withoutOriginalBackTranslation.match(/ \(한국어 역번역 참고: (.+)\)$/s);
  const withoutBackTranslation = backTranslationMatch
    ? withoutOriginalBackTranslation.slice(0, backTranslationMatch.index)
    : withoutOriginalBackTranslation;
  const backTranslation = backTranslationMatch ? backTranslationMatch[1] : null;

  // 원본 뜻 설명은 역번역 태그들보다 안쪽에 붙는다(pipeline.py의
  // _make_dual_verification_finding, findings.py의 correct_stt) — 그래서
  // 역번역 태그들을 먼저 떼어낸 뒤에 이걸 떼어내야 한다.
  const originalMeaningMatch = withoutBackTranslation.match(/ \(원본 뜻 참고: (.+)\)$/s);
  const withoutOriginalMeaning = originalMeaningMatch
    ? withoutBackTranslation.slice(0, originalMeaningMatch.index)
    : withoutBackTranslation;
  const originalMeaning = originalMeaningMatch ? originalMeaningMatch[1] : null;

  // 재질문 지시문도 여러 줄일 수 있어(검수자가 textarea에 줄바꿈 입력) 같은
  // 이유로 s 플래그가 필요하다.
  const requeryMatch = withoutOriginalMeaning.match(/^\[다시 질문: (.+?)\] /s);
  const description = requeryMatch
    ? withoutOriginalMeaning.slice(requeryMatch[0].length)
    : withoutOriginalMeaning;
  const requeryInstruction = requeryMatch ? requeryMatch[1] : null;
  return { description, backTranslation, originalBackTranslation, originalMeaning, requeryInstruction };
}

// Claude/GPT가 같은 세그먼트에 대해 서로 다르게 제안했을 때(둘 다 pending)
// 카드 두 개로 따로 보여주면 "같은 원본 문장이 여러 번 나온다"고 헷갈린다는
// 피드백이 있었다 — 그 둘을 찾아 하나의 "짝" 카드로 묶어서 표시 순서만
// 재배열한다(findings 배열 자체와 백엔드 데이터는 그대로, 화면에 어떻게
// 그릴지만 재구성). 짝의 위치는 먼저 나온(영상 순서상 앞선) 쪽 자리를 쓴다.
function groupFindingsForDisplay(findings) {
  const bySegment = {};
  for (const f of findings) {
    (bySegment[f.segment_id] ??= []).push(f);
  }
  const paired = new Set();
  const items = [];
  for (const f of findings) {
    if (paired.has(f.id)) continue;
    if (f.status === "pending" && (f.model === "claude" || f.model === "gpt")) {
      const sibling = bySegment[f.segment_id].find(
        (g) => g.id !== f.id && !paired.has(g.id) && g.status === "pending"
          && (g.model === "claude" || g.model === "gpt") && g.model !== f.model
      );
      if (sibling) {
        paired.add(f.id);
        paired.add(sibling.id);
        items.push({ type: "pair", a: f, b: sibling });
        continue;
      }
    }
    items.push({ type: "single", finding: f });
  }
  return items;
}

// STT 재검증이 성별 확인이 필요한 새 제안문구를 만들어놓고 사람 답을
// 기다리는 경우, finding 카드 안에서 바로 답할 수 있게 하는 인라인 질문
// 블록 — 풀스크린 스텝퍼(FlaggedSegmentStepper)를 다시 띄우는 대신,
// GenderQuestion을 그대로 재사용해 카드 안에서 답하고 바로 반영되게 한다.
function InlineGenderQuestion({ segment, pending, error, onResolveGender, onResolveGenderGroup }) {
  if (!segment || !segment.gender_check_needed || isGenderResolved(segment)) return null;
  const groups = segment.resolved_gender_groups_raw;
  return (
    <div
      className="mb-3 rounded-md border border-warning/40 bg-warning/10 p-3"
      onClick={(e) => e.stopPropagation()}
    >
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-warning">
        성별 확인 필요 (STT 재검증으로 새로 생긴 표현)
      </p>
      {groups && groups.length > 0 ? (
        <div className="space-y-3">
          {groups.map((group, index) => (
            <div key={index} className="rounded-md border border-border/60 bg-background p-2">
              <GenderQuestion
                heading={group.referent ? `인물 ${index + 1} (${group.referent})` : `인물 ${index + 1}`}
                words={group.words}
                wordMeanings={group.word_meanings}
                referent={null}
                resolvedGender={group.gender}
                pending={pending}
                onSelect={(gender) => onResolveGenderGroup(segment.id, index, gender)}
              />
            </div>
          ))}
        </div>
      ) : (
        <GenderQuestion
          resolvedGender={segment.resolved_gender_raw}
          pending={pending}
          onSelect={(gender) => onResolveGender(segment.id, gender)}
        />
      )}
      {error && (
        <p role="status" aria-live="polite" className="mt-2 text-xs text-destructive">{error}</p>
      )}
    </div>
  );
}

function FindingCard({
  finding, segment, isPreviewing, onPreview, reviewerName, pending, error, editing, editText, onEditTextChange, onApprove, onReject, onStartEdit, onCancelEdit, onSaveEdit,
  requerying, requeryText, requeryPending, onRequeryTextChange, onStartRequery, onCancelRequery, onSubmitRequery,
  sttEditing, sttEditText, sttPending, sttError, onSttEditTextChange, onStartSttEdit, onCancelSttEdit, onSaveSttEdit,
  genderPending, genderError, onResolveGender, onResolveGenderGroup,
}) {
  const koreanText = segment?.korean_text;
  const { description, backTranslation, originalBackTranslation, originalMeaning, requeryInstruction } =
    splitDescription(finding.description);
  const busy = pending != null;
  const canAct = Boolean(reviewerName.trim()) && !busy;
  const categoryClass = CATEGORY_BADGE_CLASS[finding.category] || FALLBACK_BADGE_CLASS;
  const statusClass = STATUS_BADGE_CLASS[finding.status] || FALLBACK_BADGE_CLASS;

  // 카드를 클릭하면 그 구간을 미리보기 재생한다 — 단, 버튼/입력 요소를 누른
  // 클릭은 승인/거부/수정 등 원래 동작을 가려서는 안 되므로 걸러낸다.
  function handleCardClick(e) {
    if (!segment || !onPreview) return;
    if (e.target.closest("button, textarea, input, a, select")) return;
    onPreview(segment);
  }

  return (
    <li
      onClick={handleCardClick}
      className={`rounded-lg border bg-card p-4 shadow-sm ${segment ? "cursor-pointer" : ""} ${
        isPreviewing ? "border-primary ring-1 ring-primary" : "border-border"
      }`}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${categoryClass}`}>
          {CATEGORY_LABELS[finding.category] || finding.category}
        </span>
        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusClass}`}>
          {STATUS_LABELS[finding.status] || finding.status}
        </span>
        {finding.model && (
          <span className="inline-flex items-center rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            {finding.model === "claude" ? "Claude" : finding.model === "gpt" ? "GPT" : finding.model}
          </span>
        )}
      </div>

      {requeryInstruction && (
        <div className="mb-3 rounded-md border border-primary/40 bg-primary/5 px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            재질문 결과 — 검토 후 승인/거부해주세요
          </p>
          <p className="mt-1 text-xs text-muted-foreground">지시: {requeryInstruction}</p>
        </div>
      )}

      <p className="mb-3 text-sm text-foreground">{description}</p>

      {/* STT 한국어 원문 — 오역처럼 보이는 finding이 사실은 STT가 잘못 알아들은
          결과일 수 있다. 검수자가 별도 STT 사이드바를 뒤지지 않고 그 자리에서
          바로 "번역이 틀렸나, STT가 틀렸나"를 가늠할 수 있게 참고용으로 붙인다. */}
      {koreanText && (
        <div className="mb-3 rounded-md border border-dashed border-accent/40 bg-accent/5 p-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-accent-foreground/80">
              STT 한국어 원문 (참고용)
            </p>
            {/* STT가 잘못 알아들은 게 원인이면, 검수자가 그 자리에서 바로
                고칠 수 있어야 한다 — 오역 finding만 승인/거부해서는 STT
                오타 자체는 안 고쳐지고 계속 남는다. */}
            {segment && !sttEditing && (
              <button
                disabled={!reviewerName.trim()}
                onClick={onStartSttEdit}
                className={`${btnBase} border border-input bg-background px-2 py-0.5 text-xs text-foreground hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50`}
              >
                STT 수정
              </button>
            )}
          </div>
          <p className="whitespace-pre-wrap text-sm text-foreground">{koreanText}</p>
          {sttEditing && (
            <div className="mt-2 space-y-2" onClick={(e) => e.stopPropagation()}>
              <Field id={`stt-${segment.id}`} label="수정된 한국어 원문">
                <textarea
                  id={`stt-${segment.id}`}
                  value={sttEditText}
                  onChange={(e) => onSttEditTextChange(e.target.value)}
                  rows={2}
                  disabled={sttPending}
                  className={inputClass}
                />
              </Field>
              <div className="flex items-center gap-2">
                <button
                  disabled={!reviewerName.trim() || !sttEditText.trim() || sttPending}
                  onClick={onSaveSttEdit}
                  className={`${primaryBtnClass} px-3 py-1.5`}
                >
                  {sttPending && <Spinner />}
                  저장 (재검증 실행)
                </button>
                <button disabled={sttPending} onClick={onCancelSttEdit} className={ghostBtnClass}>
                  취소
                </button>
                {sttError && (
                  <span role="status" aria-live="polite" className="text-xs text-destructive">
                    {sttError}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <InlineGenderQuestion
        segment={segment}
        pending={genderPending}
        error={genderError}
        onResolveGender={onResolveGender}
        onResolveGenderGroup={onResolveGenderGroup}
      />

      {/* 원본/제안 대비: 데스크톱에서 나란히(2열), 좁은 화면에서는 세로로 쌓임 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-md border border-border bg-muted/40 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">원본</p>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.original_text}</p>
          {originalMeaning && (
            <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
              원본 뜻: {originalMeaning}
            </p>
          )}
          {originalBackTranslation && (
            <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
              역번역 참고: {originalBackTranslation}
            </p>
          )}
        </div>
        <div className="rounded-md border border-primary/30 bg-primary/5 p-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-primary">제안</p>
            <CharCount text={finding.suggested_text} />
          </div>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.suggested_text}</p>
          {backTranslation && (
            <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
              역번역 참고: {backTranslation}
            </p>
          )}
        </div>
      </div>

      {/* 실제로 저장된 최종 텍스트 — AI 제안(suggested_text)과 다를 수 있으니
          (검수자가 직접 고쳤거나, 승인 시점에 50자 제약 위반이라 자동으로
          줄었거나) 다를 때만 별도로 보여준다 (export 시 실제 반영되는 텍스트). */}
      {finding.final_text && finding.final_text !== finding.suggested_text && (
        <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-warning">
            저장된 최종 텍스트
            {finding.status === "approved" && " (글자수 제약으로 자동 축약됨)"}
          </p>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.final_text}</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button disabled={!canAct} onClick={onApprove} className={approveBtnClass}>
          {pending === "approved" && <Spinner />}
          승인
        </button>
        <button disabled={!canAct} onClick={onReject} className={rejectBtnClass}>
          {pending === "rejected" && <Spinner />}
          거부
        </button>
        <button disabled={!canAct} onClick={onStartEdit} className={modifyBtnClass}>
          수정
        </button>
        {isRequeryable(finding) && (
          <button disabled={!canAct} onClick={onStartRequery} className={ghostBtnClass}>
            다시 질문
          </button>
        )}
        {error && (
          <span role="status" aria-live="polite" className="text-xs text-destructive">
            {error}
          </span>
        )}
      </div>

      {editing && (
        <div className="mt-3 space-y-2 rounded-md border border-border bg-background p-3">
          <Field id={`edit-${finding.id}`} label="수정된 텍스트">
            <textarea
              id={`edit-${finding.id}`}
              value={editText}
              onChange={(e) => onEditTextChange(e.target.value)}
              rows={3}
              disabled={busy}
              className={`${inputClass} font-mono`}
            />
          </Field>
          <div className="flex gap-2">
            <button
              disabled={!canAct || !editText.trim()}
              onClick={onSaveEdit}
              className={`${primaryBtnClass} px-3 py-1.5`}
            >
              {pending === "modified" && <Spinner />}
              저장
            </button>
            <button disabled={busy} onClick={onCancelEdit} className={ghostBtnClass}>
              취소
            </button>
          </div>
        </div>
      )}

      {requerying && (
        <div className="mt-3 space-y-2 rounded-md border border-border bg-background p-3">
          <Field id={`requery-${finding.id}`} label="AI에게 다시 지시할 내용">
            <textarea
              id={`requery-${finding.id}`}
              value={requeryText}
              onChange={(e) => onRequeryTextChange(e.target.value)}
              rows={2}
              disabled={requeryPending}
              placeholder="예: 더 자연스러운 표현으로, 직역투를 줄여서"
              className={inputClass}
            />
          </Field>
          <div className="flex gap-2">
            <button
              disabled={!canAct || !requeryText.trim() || requeryPending}
              onClick={onSubmitRequery}
              className={`${primaryBtnClass} px-3 py-1.5`}
            >
              {requeryPending && <Spinner />}
              재질문
            </button>
            <button disabled={requeryPending} onClick={onCancelRequery} className={ghostBtnClass}>
              취소
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

// Claude/GPT가 같은 세그먼트에 의견이 갈렸을 때(둘 다 pending) 쓰는 짝 카드 —
// "원본"/STT 원문은 한 번만, 두 후보(제안)는 나란히 보여준다. 하나를 고르면
// (onPick) 백엔드가 고른 쪽은 승인/수정, 짝은 자동 거부로 한 트랜잭션에
// 처리한다 — 같은 세그먼트에 두 finding이 동시에 "승인"으로 남아 최종
// 텍스트가 불확실해지는 걸 막는다(export.py는 승인/수정된 finding 중
// 하나를 골라야 하는데, 승인된 게 둘이면 어느 게 이길지 애매했다).
function PairedFindingCard({
  a, b, segment, isPreviewing, onPreview, reviewerName,
  pendingActions, findingErrors, editingId, editText, onEditTextChange,
  onPick, onReject, onStartEdit, onCancelEdit,
  requeryingId, requeryText, requeryPendingId, onRequeryTextChange, onStartRequery, onCancelRequery, onSubmitRequery,
  sttEditing, sttEditText, sttPending, sttError, onSttEditTextChange, onStartSttEdit, onCancelSttEdit, onSaveSttEdit,
  genderPending, genderError, onResolveGender, onResolveGenderGroup,
}) {
  const koreanText = segment?.korean_text;
  // 원본 뜻은 Claude/GPT 어느 쪽 설명이든 같은 원본 문장을 가리키므로 a쪽만
  // 대표로 쓴다(원본 텍스트 자체를 a.original_text 하나로 쓰는 것과 동일한 이유).
  const { originalMeaning } = splitDescription(a.description);

  function handleCardClick(e) {
    if (!segment || !onPreview) return;
    if (e.target.closest("button, textarea, input, a, select")) return;
    onPreview(segment);
  }

  function renderCandidate(finding, label) {
    const { description, backTranslation, requeryInstruction } = splitDescription(finding.description);
    const pending = pendingActions[finding.id] ?? null;
    const error = findingErrors[finding.id];
    const editing = editingId === finding.id;
    const requerying = requeryingId === finding.id;
    const requeryPending = requeryPendingId === finding.id;
    const busy = pending != null;
    const canAct = Boolean(reviewerName.trim()) && !busy;
    const categoryClass = CATEGORY_BADGE_CLASS[finding.category] || FALLBACK_BADGE_CLASS;

    return (
      <div className="rounded-md border border-border bg-muted/20 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-foreground">{label}</span>
          <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${categoryClass}`}>
            {CATEGORY_LABELS[finding.category] || finding.category}
          </span>
        </div>

        {requeryInstruction && (
          <div className="mb-2 rounded-md border border-primary/40 bg-primary/5 px-2 py-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">재질문 결과</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">지시: {requeryInstruction}</p>
          </div>
        )}

        <p className="mb-2 text-xs text-foreground">{description}</p>

        <div className="rounded-md border border-primary/30 bg-primary/5 p-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-primary">제안</p>
            <CharCount text={finding.suggested_text} />
          </div>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.suggested_text}</p>
        </div>
        {backTranslation && (
          <p className="mt-1.5 whitespace-pre-wrap text-[11px] text-muted-foreground">
            역번역 참고: {backTranslation}
          </p>
        )}

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            disabled={!canAct}
            onClick={() => onPick(finding.id)}
            className={`${approveBtnClass} px-2.5 py-1 text-xs`}
          >
            {pending === "approved" && <Spinner />}
            이 제안 채택
          </button>
          <button
            disabled={!canAct}
            onClick={() => onReject(finding.id)}
            className={`${rejectBtnClass} px-2.5 py-1 text-xs`}
          >
            {pending === "rejected" && <Spinner />}
            거부
          </button>
          <button
            disabled={!canAct}
            onClick={() => onStartEdit(finding)}
            className={`${modifyBtnClass} px-2.5 py-1 text-xs`}
          >
            수정해서 채택
          </button>
          {isRequeryable(finding) && (
            <button
              disabled={!canAct}
              onClick={() => onStartRequery(finding)}
              className={`${ghostBtnClass} px-2.5 py-1 text-xs`}
            >
              다시 질문
            </button>
          )}
        </div>
        {error && (
          <p role="status" aria-live="polite" className="mt-1.5 text-xs text-destructive">{error}</p>
        )}

        {editing && (
          <div className="mt-2 space-y-2 rounded-md border border-border bg-background p-2">
            <Field id={`edit-${finding.id}`} label="수정된 텍스트">
              <textarea
                id={`edit-${finding.id}`}
                value={editText}
                onChange={(e) => onEditTextChange(e.target.value)}
                rows={3}
                disabled={busy}
                className={`${inputClass} font-mono`}
              />
            </Field>
            <div className="flex gap-2">
              <button
                disabled={!canAct || !editText.trim()}
                onClick={() => onPick(finding.id, editText)}
                className={`${primaryBtnClass} px-3 py-1.5 text-xs`}
              >
                {pending === "modified" && <Spinner />}
                저장 (짝은 자동 거부)
              </button>
              <button disabled={busy} onClick={onCancelEdit} className={`${ghostBtnClass} text-xs`}>
                취소
              </button>
            </div>
          </div>
        )}

        {requerying && (
          <div className="mt-2 space-y-2 rounded-md border border-border bg-background p-2">
            <Field id={`requery-${finding.id}`} label="AI에게 다시 지시할 내용">
              <textarea
                id={`requery-${finding.id}`}
                value={requeryText}
                onChange={(e) => onRequeryTextChange(e.target.value)}
                rows={2}
                disabled={requeryPending}
                placeholder="예: 더 자연스러운 표현으로, 직역투를 줄여서"
                className={inputClass}
              />
            </Field>
            <div className="flex gap-2">
              <button
                disabled={!canAct || !requeryText.trim() || requeryPending}
                onClick={() => onSubmitRequery(finding.id)}
                className={`${primaryBtnClass} px-3 py-1.5 text-xs`}
              >
                {requeryPending && <Spinner />}
                재질문
              </button>
              <button disabled={requeryPending} onClick={onCancelRequery} className={`${ghostBtnClass} text-xs`}>
                취소
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <li
      onClick={handleCardClick}
      className={`rounded-lg border bg-card p-4 shadow-sm ${segment ? "cursor-pointer" : ""} ${
        isPreviewing ? "border-primary ring-1 ring-primary" : "border-border"
      }`}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center rounded-full border border-accent/40 bg-accent/10 px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">
          Claude/GPT 의견 다름 — 하나를 선택해주세요
        </span>
      </div>

      {koreanText && (
        <div className="mb-3 rounded-md border border-dashed border-accent/40 bg-accent/5 p-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-accent-foreground/80">
              STT 한국어 원문 (참고용)
            </p>
            {segment && !sttEditing && (
              <button
                disabled={!reviewerName.trim()}
                onClick={onStartSttEdit}
                className={`${btnBase} border border-input bg-background px-2 py-0.5 text-xs text-foreground hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50`}
              >
                STT 수정
              </button>
            )}
          </div>
          <p className="whitespace-pre-wrap text-sm text-foreground">{koreanText}</p>
          {sttEditing && (
            <div className="mt-2 space-y-2" onClick={(e) => e.stopPropagation()}>
              <Field id={`stt-${segment.id}`} label="수정된 한국어 원문">
                <textarea
                  id={`stt-${segment.id}`}
                  value={sttEditText}
                  onChange={(e) => onSttEditTextChange(e.target.value)}
                  rows={2}
                  disabled={sttPending}
                  className={inputClass}
                />
              </Field>
              <div className="flex items-center gap-2">
                <button
                  disabled={!reviewerName.trim() || !sttEditText.trim() || sttPending}
                  onClick={onSaveSttEdit}
                  className={`${primaryBtnClass} px-3 py-1.5`}
                >
                  {sttPending && <Spinner />}
                  저장 (재검증 실행)
                </button>
                <button disabled={sttPending} onClick={onCancelSttEdit} className={ghostBtnClass}>
                  취소
                </button>
                {sttError && (
                  <span role="status" aria-live="polite" className="text-xs text-destructive">
                    {sttError}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <InlineGenderQuestion
        segment={segment}
        pending={genderPending}
        error={genderError}
        onResolveGender={onResolveGender}
        onResolveGenderGroup={onResolveGenderGroup}
      />

      <div className="mb-3 rounded-md border border-border bg-muted/40 p-3">
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">원본</p>
        <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{a.original_text}</p>
        {originalMeaning && (
          <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
            원본 뜻: {originalMeaning}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {renderCandidate(a, a.model === "claude" ? "Claude" : "GPT")}
        {renderCandidate(b, b.model === "claude" ? "Claude" : "GPT")}
      </div>
    </li>
  );
}

// 좌측 컬럼: finding 카드를 클릭하면 그 구간이 재생되는 영상 미리보기.
// 예전엔 findings 목록 위에 붙어 있었으나, 화면 좌측 "사실 확인" 자리로
// 옮겼다(더 눈에 잘 띄고, 스크롤해도 sticky로 계속 보임).
function VideoPreviewPanel({ videoProxyUrl, previewSegment, videoRef }) {
  if (!videoProxyUrl) {
    return (
      <aside className="lg:sticky lg:top-6 lg:self-start">
        <div className="flex aspect-video items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
          영상 프록시를 사용할 수 없습니다.
        </div>
      </aside>
    );
  }
  return (
    <aside className="lg:sticky lg:top-6 lg:self-start">
      <div className="rounded-lg border border-border bg-card p-3 shadow-sm">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {previewSegment ? "구간 미리보기" : "finding 카드를 클릭하면 그 구간이 재생됩니다"}
        </p>
        <video
          ref={videoRef}
          src={videoProxyUrl}
          controls
          playsInline
          className="w-full rounded-md border border-border bg-black"
        />
      </div>
    </aside>
  );
}

export default function ReviewView({ targetVersionId, onBack }) {
  const [findings, setFindings] = useState(null); // null = 로딩 중
  const [loadError, setLoadError] = useState(null);
  const [reviewerName, setReviewerName] = useState("");
  const [pendingActions, setPendingActions] = useState({}); // findingId -> action in flight
  const [findingErrors, setFindingErrors] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [requeryingId, setRequeryingId] = useState(null);
  const [requeryText, setRequeryText] = useState("");
  const [requeryPendingId, setRequeryPendingId] = useState(null);
  // STT 원문 수정은 finding이 아니라 segment 단위라 별도 state로 관리한다
  // (editingId/requeryingId는 finding.id 기준이라 재사용하면 segment.id와
  // 섞일 수 있음).
  const [sttEditingId, setSttEditingId] = useState(null);
  const [sttEditText, setSttEditText] = useState("");
  const [sttPendingId, setSttPendingId] = useState(null);
  const [sttErrors, setSttErrors] = useState({});
  // STT 재검증이 성별 확인이 필요한 새 제안문구를 만들어놓고 사람 답을
  // 기다리는 경우, finding 카드 안에서 바로 답하기 위한 state — segment.id
  // 기준이라 sttPendingId/sttErrors와 같은 패턴이다.
  const [genderPendingId, setGenderPendingId] = useState(null);
  const [genderErrors, setGenderErrors] = useState({});
  const [exportStatus, setExportStatus] = useState({ kind: "idle" });
  const [exportResult, setExportResult] = useState(null);
  const [pipelineWarnings, setPipelineWarnings] = useState([]);
  const [videoProxyUrl, setVideoProxyUrl] = useState(null);
  // segment.start/end는 대상언어 SRT 시계다 — 영상을 잘라 올려 SRT와 영상
  // 파일 시계가 어긋나 있으면(detect_global_offset이 감지) seek 시 이 값을
  // 빼서 영상 파일 자체의 시계로 변환해야 한다.
  const [videoOffsetSeconds, setVideoOffsetSeconds] = useState(0);
  const [previewSegment, setPreviewSegment] = useState(null);
  const previewVideoRef = useRef(null);

  // finding 카드에 STT 한국어 원문을 참고용으로 보여주고 미리보기 재생
  // 구간(start/end)을 얻기 위한 세그먼트 목록.
  const [segments, setSegments] = useState(null);
  const [excludePendingId, setExcludePendingId] = useState(null);
  const [excludeErrors, setExcludeErrors] = useState({});

  useEffect(() => {
    let cancelled = false;
    setFindings(null);
    setLoadError(null);
    getFindings(targetVersionId)
      .then((data) => {
        if (!cancelled) setFindings(data);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message ?? "findings를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [targetVersionId]);

  useEffect(() => {
    let cancelled = false;
    getTargetVersion(targetVersionId)
      .then((data) => {
        if (!cancelled) {
          setPipelineWarnings(data.warnings ?? []);
          setVideoProxyUrl(data.video_proxy_url ?? null);
          setVideoOffsetSeconds(data.video_offset_seconds ?? 0);
        }
      })
      .catch(() => {
        // 경고 배너는 부가 정보라 실패해도 화면 전체를 막지 않는다.
      });
    return () => {
      cancelled = true;
    };
  }, [targetVersionId]);

  useEffect(() => {
    let cancelled = false;
    setSegments(null);

    listSegments(targetVersionId)
      .then((data) => {
        if (!cancelled) setSegments(data);
      })
      .catch(() => {
        // STT 원문/미리보기는 부가 정보라 실패해도 화면 전체를 막지 않는다.
      });

    return () => {
      cancelled = true;
    };
  }, [targetVersionId]);

  // finding 카드를 클릭하면 그 구간으로 이동해 자동재생하고, 구간 끝에서
  // 멈춘다 — FlaggedSegmentStepper와 같은 <video> 재사용 패턴(리로드 없이
  // currentTime만 이동)이지만, 저기는 반복재생이고 여기는 끝에서 정지한다.
  useEffect(() => {
    const video = previewVideoRef.current;
    if (!video || !previewSegment) return undefined;
    const seekStart = previewSegment.start - videoOffsetSeconds;
    const seekEnd = previewSegment.end - videoOffsetSeconds;
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
        // 자동재생이 브라우저 정책으로 거부될 수 있다(이 미리보기는 음소거가
        // 아니라 오디오를 들어야 해서 muted를 안 씀) — controls로 직접 재생 가능.
      });
    }
    video.addEventListener("timeupdate", handleTimeUpdate);
    return () => video.removeEventListener("timeupdate", handleTimeUpdate);
  }, [previewSegment?.id, previewSegment?.start, previewSegment?.end, videoOffsetSeconds]);

  async function handleAction(findingId, action, finalText = "") {
    setFindingErrors((prev) => ({ ...prev, [findingId]: null }));
    setPendingActions((prev) => ({ ...prev, [findingId]: action }));
    try {
      await submitReviewAction(findingId, action, reviewerName, finalText);
      setFindings(await getFindings(targetVersionId));
      // 저장이 끝난 finding이 여전히 편집 중인 finding일 때만 편집 패널을 닫는다.
      // 저장 요청이 진행되는 동안 다른 finding의 수정 패널이 열렸다면(editingId가
      // 바뀌었다면) 그 미저장 초안을 여기서 지우면 안 된다.
      if (action === "modified") {
        setEditingId((cur) => (cur === findingId ? null : cur));
      }
    } catch (err) {
      setFindingErrors((prev) => ({
        ...prev,
        [findingId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setPendingActions((prev) => {
        const next = { ...prev };
        delete next[findingId];
        return next;
      });
    }
  }

  // Claude/GPT 짝 카드에서 한쪽을 고른다 — finalText가 있으면 "수정해서
  // 채택"(직접 고친 문구로), 없으면 그대로 "채택"(제안 그대로 승인). 백엔드가
  // 고른 쪽 승인/수정 + 짝 자동 거부를 한 트랜잭션으로 처리한다.
  async function handlePick(findingId, otherFindingId, finalText = "") {
    setFindingErrors((prev) => ({ ...prev, [findingId]: null }));
    setPendingActions((prev) => ({ ...prev, [findingId]: finalText ? "modified" : "approved" }));
    try {
      await pickFinding(findingId, otherFindingId, reviewerName, finalText);
      setFindings(await getFindings(targetVersionId));
      setEditingId((cur) => (cur === findingId ? null : cur));
    } catch (err) {
      setFindingErrors((prev) => ({
        ...prev,
        [findingId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setPendingActions((prev) => {
        const next = { ...prev };
        delete next[findingId];
        return next;
      });
    }
  }

  // 겹치는 짝이 없는 반쪽짜리 Segment를 최종 자막에서 뺄지(또는 되돌릴지)
  // 검수자가 결정한다.
  async function handleToggleExclude(segmentId, nextExcluded) {
    setExcludeErrors((prev) => ({ ...prev, [segmentId]: null }));
    setExcludePendingId(segmentId);
    try {
      await excludeSegment(segmentId, nextExcluded);
      setSegments(await listSegments(targetVersionId));
    } catch (err) {
      setExcludeErrors((prev) => ({
        ...prev,
        [segmentId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setExcludePendingId(null);
    }
  }

  function startEdit(finding) {
    setEditingId(finding.id);
    // 이미 한 번 수정 저장된 finding을 다시 열 때는 검수자의 직전 편집(final_text)을
    // 기준으로 이어서 편집한다. AI의 원래 제안(suggested_text)으로 되돌리면 첫 편집
    // 내용을 조용히 잃게 되므로, final_text가 있을 때는 그쪽을 우선한다.
    setEditText(
      finding.status === "modified" && finding.final_text
        ? finding.final_text
        : finding.suggested_text
    );
  }

  async function handleRequery(findingId) {
    setRequeryPendingId(findingId);
    try {
      await requeryFinding(findingId, requeryText, reviewerName);
      setFindings(await getFindings(targetVersionId));
      setRequeryingId((cur) => (cur === findingId ? null : cur));
    } catch (err) {
      setFindingErrors((prev) => ({
        ...prev,
        [findingId]: err.message ?? "재질문 중 오류가 발생했습니다.",
      }));
    } finally {
      setRequeryPendingId(null);
    }
  }

  async function handleSaveSttEdit(segmentId) {
    setSttErrors((prev) => ({ ...prev, [segmentId]: null }));
    setSttPendingId(segmentId);
    try {
      const result = await correctStt(segmentId, sttEditText, reviewerName);
      setSegments((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, korean_text: result.korean_text } : s))
      );
      // STT 수정으로 번역이 다시 문제가 됐으면 백엔드가 새 pending finding을
      // 만든다(reverify_segment_after_stt_correction) — 목록을 다시 불러와야
      // 그 카드가 보인다.
      if (result.new_finding) {
        setFindings(await getFindings(targetVersionId));
      }
      setSttEditingId(null);
    } catch (err) {
      setSttErrors((prev) => ({
        ...prev,
        [segmentId]: err.message ?? "STT 수정 중 오류가 발생했습니다.",
      }));
    } finally {
      setSttPendingId(null);
    }
  }

  // 세그먼트의 성별을 답하면(단일 인물), 백엔드가 그 세그먼트의 pending
  // finding 제안문구도 이미 반영해뒀으므로 segments/findings를 모두
  // 다시 불러온다 — 카드의 "제안" 텍스트가 즉시 갱신되게.
  async function handleResolveGenderInline(segmentId, gender) {
    setGenderErrors((prev) => ({ ...prev, [segmentId]: null }));
    setGenderPendingId(segmentId);
    try {
      const updated = await resolveGender(segmentId, gender);
      setSegments((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, ...updated } : s))
      );
      setFindings(await getFindings(targetVersionId));
    } catch (err) {
      setGenderErrors((prev) => ({
        ...prev,
        [segmentId]: err.message ?? "성별 확인 중 오류가 발생했습니다.",
      }));
    } finally {
      setGenderPendingId(null);
    }
  }

  async function handleResolveGenderGroupInline(segmentId, groupIndex, gender) {
    setGenderErrors((prev) => ({ ...prev, [segmentId]: null }));
    setGenderPendingId(segmentId);
    try {
      const updated = await resolveGenderGroup(segmentId, groupIndex, gender);
      setSegments((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, ...updated } : s))
      );
      setFindings(await getFindings(targetVersionId));
    } catch (err) {
      setGenderErrors((prev) => ({
        ...prev,
        [segmentId]: err.message ?? "성별 확인 중 오류가 발생했습니다.",
      }));
    } finally {
      setGenderPendingId(null);
    }
  }

  async function handleExport() {
    setExportStatus({ kind: "loading" });
    try {
      const result = await exportTargetVersion(targetVersionId);
      const warnings = result.format_warnings ?? [];
      if (warnings.length > 0) {
        const detail = warnings.map((w) => `- [${w.rule}] ${w.detail}`).join("\n");
        const proceed = window.confirm(
          `포맷 경고 ${warnings.length}건이 있습니다:\n${detail}\n\n그래도 내보내시겠습니까?`
        );
        if (!proceed) {
          setExportStatus({ kind: "idle" });
          return;
        }
      }
      setExportResult(result);
      setExportStatus({ kind: "idle" });
    } catch (err) {
      setExportStatus({ kind: "error", message: err.message ?? "내보내기 중 오류가 발생했습니다." });
    }
  }

  const isExporting = exportStatus.kind === "loading";
  const formatWarnings = exportResult?.format_warnings ?? [];
  // Finding 카드에 STT 한국어 원문 + 그 구간 영상을 참고용으로 보여주기 위한
  // 조회용 — 이미 STT 사이드바에서 받아온 segments를 그대로 재사용한다
  // (추가 API 호출 없음).
  const segmentsById = segments
    ? Object.fromEntries(segments.map((s) => [s.id, s]))
    : {};

  // 겹치는 짝이 없는 반쪽짜리 Segment(한국어만 있거나 대상언어만 있는
  // 경우) — 시간 순으로 보여준다.
  const halfPairSegments = segments
    ? segments.filter((s) => !s.korean_text?.trim() || !s.target_text?.trim())
        .sort((a, b) => a.start - b.start)
    : [];

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card px-6 py-4">
        <div className="mx-auto flex max-w-6xl flex-wrap items-end justify-between gap-4">
          <div>
            <button
              onClick={onBack}
              className="mb-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              &larr; 목록으로
            </button>
            <h1 className="text-xl font-semibold text-card-foreground">리뷰 — Findings</h1>
          </div>
          <div className="w-full max-w-xs">
            <Field id="reviewer-name" label="검수자 이름">
              <input
                id="reviewer-name"
                value={reviewerName}
                onChange={(e) => setReviewerName(e.target.value)}
                placeholder="이름을 입력하세요"
                className={inputClass}
              />
            </Field>
          </div>
        </div>
      </header>

      {pipelineWarnings.length > 0 && (
        <div className="mx-auto max-w-6xl px-6 pt-4">
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <p className="font-medium">일부 분석 단계 실패</p>
            <ul className="mt-1 list-disc pl-5">
              {pipelineWarnings.map((w, i) => (
                <li key={i}>{w.stage}: {w.message}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* 본문을 좌측 영상 미리보기 / 우측 Findings·내보내기 본문으로 분리한다.
          좁은 화면에서는 세로로 쌓이며 미리보기가 먼저 나온다. */}
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[320px_1fr]">
          <VideoPreviewPanel
            videoProxyUrl={videoProxyUrl}
            previewSegment={previewSegment}
            videoRef={previewVideoRef}
          />

          <div className="space-y-8">
            <section aria-labelledby="findings-heading">
              <h2 id="findings-heading" className="mb-3 text-lg font-semibold text-foreground">
                Findings {findings ? `(${findings.length})` : ""}
              </h2>

              {findings === null && !loadError && (
                <p className="text-sm text-muted-foreground">불러오는 중...</p>
              )}
              {loadError && (
                <p role="status" aria-live="polite" className="text-sm text-destructive">
                  {loadError}
                </p>
              )}
              {findings && findings.length === 0 && (
                <p className="text-sm text-muted-foreground">표시할 finding이 없습니다.</p>
              )}

              {findings && findings.length > 0 && (
                <ul className="space-y-4">
                  {groupFindingsForDisplay(findings).map((item) => {
                    if (item.type === "pair") {
                      const { a, b } = item;
                      return (
                        <PairedFindingCard
                          key={a.id}
                          a={a}
                          b={b}
                          segment={segmentsById[a.segment_id]}
                          isPreviewing={previewSegment?.id === a.segment_id}
                          onPreview={setPreviewSegment}
                          reviewerName={reviewerName}
                          pendingActions={pendingActions}
                          findingErrors={findingErrors}
                          editingId={editingId}
                          editText={editText}
                          onEditTextChange={setEditText}
                          onPick={(findingId, finalText) => {
                            const otherId = findingId === a.id ? b.id : a.id;
                            handlePick(findingId, otherId, finalText);
                          }}
                          onReject={(findingId) => handleAction(findingId, "rejected")}
                          onStartEdit={startEdit}
                          onCancelEdit={() => setEditingId(null)}
                          requeryingId={requeryingId}
                          requeryText={requeryText}
                          requeryPendingId={requeryPendingId}
                          onRequeryTextChange={setRequeryText}
                          onStartRequery={(finding) => {
                            setRequeryingId(finding.id);
                            setRequeryText("");
                          }}
                          onCancelRequery={() => setRequeryingId(null)}
                          onSubmitRequery={(findingId) => handleRequery(findingId)}
                          sttEditing={sttEditingId === a.segment_id}
                          sttEditText={sttEditText}
                          sttPending={sttPendingId === a.segment_id}
                          sttError={sttErrors[a.segment_id]}
                          onSttEditTextChange={setSttEditText}
                          onStartSttEdit={() => {
                            setSttEditingId(a.segment_id);
                            setSttEditText(segmentsById[a.segment_id]?.korean_text ?? "");
                          }}
                          onCancelSttEdit={() => setSttEditingId(null)}
                          onSaveSttEdit={() => handleSaveSttEdit(a.segment_id)}
                          genderPending={genderPendingId === a.segment_id}
                          genderError={genderErrors[a.segment_id]}
                          onResolveGender={handleResolveGenderInline}
                          onResolveGenderGroup={handleResolveGenderGroupInline}
                        />
                      );
                    }
                    const f = item.finding;
                    return (
                      <FindingCard
                        key={f.id}
                        finding={f}
                        segment={segmentsById[f.segment_id]}
                        isPreviewing={previewSegment?.id === f.segment_id}
                        onPreview={setPreviewSegment}
                        reviewerName={reviewerName}
                        pending={pendingActions[f.id] ?? null}
                        error={findingErrors[f.id]}
                        editing={editingId === f.id}
                        editText={editText}
                        onEditTextChange={setEditText}
                        onApprove={() => handleAction(f.id, "approved")}
                        onReject={() => handleAction(f.id, "rejected")}
                        onStartEdit={() => startEdit(f)}
                        onCancelEdit={() => setEditingId(null)}
                        onSaveEdit={() => handleAction(f.id, "modified", editText)}
                        requerying={requeryingId === f.id}
                        requeryText={requeryText}
                        requeryPending={requeryPendingId === f.id}
                        onRequeryTextChange={setRequeryText}
                        onStartRequery={() => {
                          setRequeryingId(f.id);
                          setRequeryText("");
                        }}
                        onCancelRequery={() => setRequeryingId(null)}
                        onSubmitRequery={() => handleRequery(f.id)}
                        sttEditing={sttEditingId === f.segment_id}
                        sttEditText={sttEditText}
                        sttPending={sttPendingId === f.segment_id}
                        sttError={sttErrors[f.segment_id]}
                        onSttEditTextChange={setSttEditText}
                        onStartSttEdit={() => {
                          setSttEditingId(f.segment_id);
                          setSttEditText(segmentsById[f.segment_id]?.korean_text ?? "");
                        }}
                        onCancelSttEdit={() => setSttEditingId(null)}
                        onSaveSttEdit={() => handleSaveSttEdit(f.segment_id)}
                        genderPending={genderPendingId === f.segment_id}
                        genderError={genderErrors[f.segment_id]}
                        onResolveGender={handleResolveGenderInline}
                        onResolveGenderGroup={handleResolveGenderGroupInline}
                      />
                    );
                  })}
                </ul>
              )}
            </section>

            {halfPairSegments.length > 0 && (
              <section className="rounded-lg border border-border bg-card p-6 shadow-sm">
                <div className="mb-4">
                  <h2 className="text-lg font-semibold text-card-foreground">
                    확인 필요 (짝 없는 줄) {halfPairSegments.length}건
                  </h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    한국어와 대상언어 중 한쪽만 있어 자동으로 짝을 못 찾은 줄입니다.
                    필요 없는 줄은 제외할 수 있습니다.
                  </p>
                </div>
                <ul className="space-y-3">
                  {halfPairSegments.map((seg) => {
                    const hasTarget = Boolean(seg.target_text?.trim());
                    const pending = excludePendingId === seg.id;
                    return (
                      <li
                        key={seg.id}
                        className={`rounded-md border p-3 ${
                          seg.excluded ? "border-border bg-muted/30 opacity-60" : "border-border bg-muted/10"
                        }`}
                      >
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            {hasTarget ? "대상언어만 있음 (최종 자막에 포함됨)" : "한국어만 있음 (최종 자막에 영향 없음)"}
                          </span>
                          {seg.excluded && (
                            <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                              제외됨
                            </span>
                          )}
                        </div>
                        {seg.korean_text && (
                          <p className="whitespace-pre-wrap text-sm text-foreground">{seg.korean_text}</p>
                        )}
                        {seg.target_text && (
                          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{seg.target_text}</p>
                        )}
                        <div className="mt-2 flex items-center gap-2">
                          <button
                            disabled={pending}
                            onClick={() => handleToggleExclude(seg.id, !seg.excluded)}
                            className={`${btnBase} border border-input bg-background px-2.5 py-1 text-xs text-foreground hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50`}
                          >
                            {pending && <Spinner />}
                            {seg.excluded ? "제외 취소" : "제외"}
                          </button>
                          {excludeErrors[seg.id] && (
                            <span role="status" aria-live="polite" className="text-xs text-destructive">
                              {excludeErrors[seg.id]}
                            </span>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            <section className="rounded-lg border border-border bg-card p-6 shadow-sm">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-card-foreground">최종 SRT 내보내기</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    승인/수정된 텍스트를 반영한 최종 자막을 생성합니다.
                  </p>
                </div>
                <button onClick={handleExport} disabled={isExporting} className={primaryBtnClass}>
                  {isExporting && <Spinner />}
                  내보내기
                </button>
              </div>

              {exportStatus.kind === "error" && (
                <p role="status" aria-live="polite" className="mb-3 text-sm text-destructive">
                  {exportStatus.message}
                </p>
              )}

              {exportResult && (
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-3 text-sm">
                    <span className="rounded-md bg-muted px-3 py-1.5 text-muted-foreground">
                      발견 건수: <strong className="text-foreground">{exportResult.stats.finding_count}</strong>
                    </span>
                    <span className="rounded-md bg-muted px-3 py-1.5 text-muted-foreground">
                      반영율:{" "}
                      <strong className="text-foreground">
                        {Math.round(exportResult.stats.reflection_rate * 100)}%
                      </strong>
                    </span>
                  </div>

                  {formatWarnings.length > 0 && (
                    <div className="rounded-md border border-warning/40 bg-warning/10 p-3">
                      <p className="mb-1 text-sm font-medium text-warning">
                        포맷 경고 {formatWarnings.length}건 (내보내기는 차단되지 않음)
                      </p>
                      <ul className="ml-4 list-disc space-y-0.5 text-xs text-warning">
                        {formatWarnings.map((w, i) => (
                          <li key={`${w.segment_id}-${i}`}>
                            [{w.rule}] {w.detail}
                            {w.auto_fixed ? " (자동 수정됨)" : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/30 p-3 font-mono text-xs text-foreground">
                    {exportResult.srt}
                  </pre>
                </div>
              )}
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
