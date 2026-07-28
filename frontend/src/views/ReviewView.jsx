import { useEffect, useState } from "react";
import { getFindings, submitReviewAction, exportTargetVersion } from "../api.js";

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

  async function handleAction(findingId, action, finalText = "") {
    setFindingErrors((prev) => ({ ...prev, [findingId]: null }));
    setPendingActions((prev) => ({ ...prev, [findingId]: action }));
    try {
      await submitReviewAction(findingId, action, reviewerName, finalText);
      setFindings(await getFindings(targetVersionId));
      if (action === "modified") setEditingId(null);
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
    setEditText(finding.suggested_text);
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
        <div className="mx-auto flex max-w-5xl flex-wrap items-end justify-between gap-4">
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

      <main className="mx-auto max-w-5xl space-y-8 px-6 py-8">
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
      </main>
    </div>
  );
}
