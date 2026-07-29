// findings 승인/거부/수정, 인물/관계 확인, STT 수정, export를 담당하는 검수 화면.

import { useEffect, useState } from "react";
import {
  getFindings,
  submitReviewAction,
  exportTargetVersion,
  listCharacters,
  listRelationships,
  listSegments,
  confirmGender,
  confirmFormality,
  correctStt,
} from "../api.js";

// 카테고리 라벨/색상: Task 21 Step 0에서 확정한 6종 팔레트를 그대로 재사용한다
// (frontend/tailwind.config.js의 theme.extend.colors.finding.*). 새 색상을 만들지 않는다.
const CATEGORY_LABELS = {
  gender: "성별",
  register: "격식체",
  translation: "번역",
  localization: "로컬라이제이션",
  sensitivity: "민감어",
  formatting: "포맷팅",
};

const CATEGORY_BADGE_CLASS = {
  gender: "bg-finding-gender-bg text-finding-gender-text border-finding-gender-border",
  register: "bg-finding-register-bg text-finding-register-text border-finding-register-border",
  translation: "bg-finding-translation-bg text-finding-translation-text border-finding-translation-border",
  localization: "bg-finding-localization-bg text-finding-localization-text border-finding-localization-border",
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

// "사실 확인" 사이드바(인물 성별/관계 격식/STT 원문 교정)는 Findings 목록(승인·거부·수정
// 대상, primary/success/destructive/warning 토큰 사용)과 절대 혼동되면 안 되므로
// (design note: 검수자가 "사실 확인"과 "번역 승인/거부"를 구분해야 함) 이 영역
// 전용으로 지금까지 쓰이지 않은 accent 토큰 + 점선 테두리 + 별도 사이드바 레이아웃을
// 사용해 시각적으로 뚜렷이 분리한다 (ui-ux-pro-max 가이드로 결정).
const factSmallBtnClass =
  "inline-flex items-center justify-center rounded-md border px-2.5 py-1 text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50 border-accent/50 bg-accent/10 text-accent-foreground " +
  "hover:bg-accent/25";
const factConfirmedBadgeClass =
  "inline-flex items-center rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-xs font-medium text-success";
const factRowClass = "rounded-md border border-dashed border-accent/40 bg-accent/5 p-3";

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

function FindingCard({ finding, reviewerName, pending, error, editing, editText, onEditTextChange, onApprove, onReject, onStartEdit, onCancelEdit, onSaveEdit }) {
  const busy = pending != null;
  const canAct = Boolean(reviewerName.trim()) && !busy;
  const categoryClass = CATEGORY_BADGE_CLASS[finding.category] || FALLBACK_BADGE_CLASS;
  const statusClass = STATUS_BADGE_CLASS[finding.status] || FALLBACK_BADGE_CLASS;

  return (
    <li className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${categoryClass}`}>
          {CATEGORY_LABELS[finding.category] || finding.category}
        </span>
        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusClass}`}>
          {STATUS_LABELS[finding.status] || finding.status}
        </span>
      </div>

      <p className="mb-3 text-sm text-foreground">{finding.description}</p>

      {/* 원본/제안 대비: 데스크톱에서 나란히(2열), 좁은 화면에서는 세로로 쌓임 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-md border border-border bg-muted/40 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">원본</p>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.original_text}</p>
        </div>
        <div className="rounded-md border border-primary/30 bg-primary/5 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-primary">제안</p>
          <p className="whitespace-pre-wrap font-mono text-sm text-foreground">{finding.suggested_text}</p>
        </div>
      </div>

      {/* 검수자가 실제로 저장한 최종 텍스트 — AI 제안(suggested_text)과 다를 수 있으므로
          "수정됨" 상태일 때는 별도로 보여준다 (export 시 실제 반영되는 텍스트). */}
      {finding.status === "modified" && finding.final_text && (
        <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-warning">저장된 최종 텍스트</p>
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
    </li>
  );
}

function FactSectionHeading({ id, children }) {
  return (
    <h3 id={id} className="mb-2 text-xs font-semibold uppercase tracking-wide text-accent-foreground/80">
      {children}
    </h3>
  );
}

function CharacterRow({ character, pending, error, canAct, onConfirm }) {
  const busy = pending != null;
  const disabled = busy || !canAct;
  return (
    <li className={factRowClass}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{character.label}</span>
        {character.confirmed_gender ? (
          <span className={factConfirmedBadgeClass}>
            {character.confirmed_gender === "male" ? "남성 확인됨" : "여성 확인됨"}
          </span>
        ) : (
          <div className="flex gap-1.5">
            <button disabled={disabled} onClick={() => onConfirm("male")} className={factSmallBtnClass}>
              {pending === "male" && <Spinner />} 남성
            </button>
            <button disabled={disabled} onClick={() => onConfirm("female")} className={factSmallBtnClass}>
              {pending === "female" && <Spinner />} 여성
            </button>
          </div>
        )}
      </div>
      {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
    </li>
  );
}

function RelationshipRow({ relationship, pending, error, canAct, onConfirm }) {
  const busy = pending != null;
  const disabled = busy || !canAct;
  return (
    <li className={factRowClass}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          {relationship.speaker_label ?? "?"} → {relationship.addressee_label ?? "?"}
        </span>
        {relationship.confirmed_formality_level ? (
          <span className={factConfirmedBadgeClass}>
            {relationship.confirmed_formality_level === "formal" ? "격식체 확인됨" : "비격식체 확인됨"}
          </span>
        ) : (
          <div className="flex gap-1.5">
            <button disabled={disabled} onClick={() => onConfirm("formal")} className={factSmallBtnClass}>
              {pending === "formal" && <Spinner />} 격식체
            </button>
            <button disabled={disabled} onClick={() => onConfirm("informal")} className={factSmallBtnClass}>
              {pending === "informal" && <Spinner />} 비격식체
            </button>
          </div>
        )}
      </div>
      {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
    </li>
  );
}

function SttSegmentRow({ segment, editing, editText, pending, error, canAct, onEditTextChange, onStartEdit, onCancelEdit, onSaveEdit }) {
  const disabled = pending || !canAct;
  return (
    <li className={factRowClass}>
      <p className="whitespace-pre-wrap text-sm text-foreground">{segment.korean_text}</p>
      {!editing ? (
        <div className="mt-2">
          <button disabled={disabled} onClick={onStartEdit} className={factSmallBtnClass}>
            STT 수정
          </button>
        </div>
      ) : (
        <div className="mt-2 space-y-2">
          <textarea
            value={editText}
            onChange={(e) => onEditTextChange(e.target.value)}
            rows={2}
            disabled={pending}
            className={`${inputClass} text-sm`}
            aria-label="수정할 한국어 원문"
          />
          <div className="flex gap-2">
            <button
              disabled={disabled || !editText.trim()}
              onClick={onSaveEdit}
              className={`${factSmallBtnClass} border-primary/50 bg-primary/10 text-primary hover:bg-primary/20`}
            >
              {pending && <Spinner />} 저장
            </button>
            <button disabled={pending} onClick={onCancelEdit} className={ghostBtnClass}>
              취소
            </button>
          </div>
        </div>
      )}
      {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
    </li>
  );
}

// "사실 확인" 사이드바: 인물 성별 확인 / 관계 격식 확인 / STT 원문 인라인 수정을 한
// 영역에 모아 Findings(번역 승인/거부)와 명확히 구분한다. 세 섹션 모두 확인할 대상이
// 없으면(모두 이미 확인됐거나 데이터가 없으면) 조용히 숨긴다.
function FactConfirmationPanel({
  reviewerName,
  characters, charactersError, characterPending, characterErrors, onConfirmCharacter,
  relationships, relationshipsError, relationshipPending, relationshipErrors, onConfirmRelationship,
  segments, segmentsError, sttEditingId, sttEditText, sttPending, sttErrors,
  onSttEditTextChange, onSttStartEdit, onSttCancelEdit, onSttSaveEdit,
}) {
  const loading = characters === null && relationships === null && segments === null;
  const canAct = Boolean(reviewerName.trim());

  return (
    <aside
      aria-labelledby="fact-confirmation-heading"
      className="rounded-lg border-2 border-dashed border-accent/60 bg-accent/[0.06] p-4 lg:sticky lg:top-6 lg:self-start"
    >
      <div className="mb-4">
        <span className="inline-flex items-center rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">
          사실 확인
        </span>
        <h2 id="fact-confirmation-heading" className="mt-2 text-base font-semibold text-foreground">
          인물·관계·STT 원문
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          번역 승인/거부가 아닌, 원본 사실 확인 영역입니다.
        </p>
      </div>

      {loading && <p className="text-sm text-muted-foreground">불러오는 중...</p>}

      <div className="space-y-6">
        {characters && characters.length > 0 && (
          <section aria-labelledby="fact-characters-heading">
            <FactSectionHeading id="fact-characters-heading">인물 성별 확인</FactSectionHeading>
            <ul className="space-y-2">
              {characters.map((c) => (
                <CharacterRow
                  key={c.id}
                  character={c}
                  pending={characterPending[c.id] ?? null}
                  error={characterErrors[c.id]}
                  canAct={canAct}
                  onConfirm={(gender) => onConfirmCharacter(c.id, gender)}
                />
              ))}
            </ul>
          </section>
        )}
        {charactersError && <p className="text-xs text-destructive">{charactersError}</p>}

        {relationships && relationships.length > 0 && (
          <section aria-labelledby="fact-relationships-heading">
            <FactSectionHeading id="fact-relationships-heading">관계 격식 확인</FactSectionHeading>
            <ul className="space-y-2">
              {relationships.map((r) => (
                <RelationshipRow
                  key={r.id}
                  relationship={r}
                  pending={relationshipPending[r.id] ?? null}
                  error={relationshipErrors[r.id]}
                  canAct={canAct}
                  onConfirm={(level) => onConfirmRelationship(r.id, level)}
                />
              ))}
            </ul>
          </section>
        )}
        {relationshipsError && <p className="text-xs text-destructive">{relationshipsError}</p>}

        {segments && segments.length > 0 && (
          <section aria-labelledby="fact-stt-heading">
            <FactSectionHeading id="fact-stt-heading">STT 원문 인라인 수정</FactSectionHeading>
            <ul className="space-y-2">
              {segments.map((s) => (
                <SttSegmentRow
                  key={s.id}
                  segment={s}
                  editing={sttEditingId === s.id}
                  editText={sttEditText}
                  pending={sttPending === s.id}
                  error={sttErrors[s.id]}
                  canAct={canAct}
                  onEditTextChange={onSttEditTextChange}
                  onStartEdit={() => onSttStartEdit(s)}
                  onCancelEdit={onSttCancelEdit}
                  onSaveEdit={() => onSttSaveEdit(s.id)}
                />
              ))}
            </ul>
          </section>
        )}
        {segmentsError && <p className="text-xs text-destructive">{segmentsError}</p>}

        {!loading &&
          characters?.length === 0 &&
          relationships?.length === 0 &&
          segments?.length === 0 &&
          !charactersError && !relationshipsError && !segmentsError && (
            <p className="text-sm text-muted-foreground">확인할 항목이 없습니다.</p>
          )}

        {!reviewerName.trim() && (
          <p className="text-xs text-muted-foreground">
            상단에 검수자 이름을 입력해야 확인/수정을 저장할 수 있습니다.
          </p>
        )}
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
  const [exportStatus, setExportStatus] = useState({ kind: "idle" });
  const [exportResult, setExportResult] = useState(null);

  // "사실 확인" 사이드바 상태 — Findings(위 state들)와는 완전히 분리된 데이터
  // 흐름을 가진다: 인물 성별 / 관계 격식 / STT 원문 세 섹션.
  const [characters, setCharacters] = useState(null); // null = 로딩 중
  const [charactersError, setCharactersError] = useState(null);
  const [characterPending, setCharacterPending] = useState({}); // characterId -> gender in flight
  const [characterErrors, setCharacterErrors] = useState({});

  const [relationships, setRelationships] = useState(null);
  const [relationshipsError, setRelationshipsError] = useState(null);
  const [relationshipPending, setRelationshipPending] = useState({}); // relationshipId -> level in flight
  const [relationshipErrors, setRelationshipErrors] = useState({});

  const [segments, setSegments] = useState(null);
  const [segmentsError, setSegmentsError] = useState(null);
  const [sttEditingId, setSttEditingId] = useState(null);
  const [sttEditText, setSttEditText] = useState("");
  const [sttPending, setSttPending] = useState(null); // segmentId in flight
  const [sttErrors, setSttErrors] = useState({});

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
    setCharacters(null);
    setCharactersError(null);
    setRelationships(null);
    setRelationshipsError(null);
    setSegments(null);
    setSegmentsError(null);

    listCharacters(targetVersionId)
      .then((data) => {
        if (!cancelled) setCharacters(data);
      })
      .catch((err) => {
        if (!cancelled) setCharactersError(err.message ?? "인물 목록을 불러오지 못했습니다.");
      });

    listRelationships(targetVersionId)
      .then((data) => {
        if (!cancelled) setRelationships(data);
      })
      .catch((err) => {
        if (!cancelled) setRelationshipsError(err.message ?? "관계 목록을 불러오지 못했습니다.");
      });

    listSegments(targetVersionId)
      .then((data) => {
        if (!cancelled) setSegments(data);
      })
      .catch((err) => {
        if (!cancelled) setSegmentsError(err.message ?? "세그먼트 목록을 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
    };
  }, [targetVersionId]);

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

  async function handleConfirmCharacter(characterId, gender) {
    setCharacterErrors((prev) => ({ ...prev, [characterId]: null }));
    setCharacterPending((prev) => ({ ...prev, [characterId]: gender }));
    try {
      await confirmGender(characterId, gender);
      setCharacters(await listCharacters(targetVersionId));
    } catch (err) {
      setCharacterErrors((prev) => ({
        ...prev,
        [characterId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setCharacterPending((prev) => {
        const next = { ...prev };
        delete next[characterId];
        return next;
      });
    }
  }

  async function handleConfirmRelationship(relationshipId, level) {
    setRelationshipErrors((prev) => ({ ...prev, [relationshipId]: null }));
    setRelationshipPending((prev) => ({ ...prev, [relationshipId]: level }));
    try {
      await confirmFormality(relationshipId, level);
      setRelationships(await listRelationships(targetVersionId));
    } catch (err) {
      setRelationshipErrors((prev) => ({
        ...prev,
        [relationshipId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setRelationshipPending((prev) => {
        const next = { ...prev };
        delete next[relationshipId];
        return next;
      });
    }
  }

  function startSttEdit(segment) {
    setSttEditingId(segment.id);
    setSttEditText(segment.korean_text);
  }

  async function handleSttSave(segmentId) {
    setSttErrors((prev) => ({ ...prev, [segmentId]: null }));
    setSttPending(segmentId);
    try {
      await correctStt(segmentId, sttEditText, reviewerName);
      setSegments(await listSegments(targetVersionId));
      setSttEditingId((cur) => (cur === segmentId ? null : cur));
    } catch (err) {
      setSttErrors((prev) => ({
        ...prev,
        [segmentId]: err.message ?? "요청 중 오류가 발생했습니다.",
      }));
    } finally {
      setSttPending(null);
    }
  }

  async function handleExport() {
    setExportStatus({ kind: "loading" });
    try {
      const result = await exportTargetVersion(targetVersionId);
      setExportResult(result);
      setExportStatus({ kind: "idle" });
    } catch (err) {
      setExportStatus({ kind: "error", message: err.message ?? "내보내기 중 오류가 발생했습니다." });
    }
  }

  const isExporting = exportStatus.kind === "loading";
  const formatWarnings = exportResult?.format_warnings ?? [];

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

      {/* 본문을 좌측 "사실 확인" 사이드바 / 우측 Findings·내보내기 본문으로 분리한다.
          두 영역은 레이아웃(사이드바 vs 본문 컬럼)과 스타일(점선 accent 테두리 vs
          실선 카드) 모두로 구분되어, 검수자가 "사실 확인"과 "번역 승인/거부"를
          섞어 보지 않도록 한다 (ui-ux-pro-max 가이드 적용). 좁은 화면에서는 세로로
          쌓이며 사실 확인 패널이 먼저 나온다. */}
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[320px_1fr]">
          <FactConfirmationPanel
            reviewerName={reviewerName}
            characters={characters}
            charactersError={charactersError}
            characterPending={characterPending}
            characterErrors={characterErrors}
            onConfirmCharacter={handleConfirmCharacter}
            relationships={relationships}
            relationshipsError={relationshipsError}
            relationshipPending={relationshipPending}
            relationshipErrors={relationshipErrors}
            onConfirmRelationship={handleConfirmRelationship}
            segments={segments}
            segmentsError={segmentsError}
            sttEditingId={sttEditingId}
            sttEditText={sttEditText}
            sttPending={sttPending}
            sttErrors={sttErrors}
            onSttEditTextChange={setSttEditText}
            onSttStartEdit={startSttEdit}
            onSttCancelEdit={() => setSttEditingId(null)}
            onSttSaveEdit={handleSttSave}
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
                  {findings.map((f) => (
                    <FindingCard
                      key={f.id}
                      finding={f}
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
                    />
                  ))}
                </ul>
              )}
            </section>

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
