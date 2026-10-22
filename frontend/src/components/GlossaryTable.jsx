import { useState } from "react";
import {
  postGlossaryEntry, patchGlossaryEntry, deleteGlossaryEntry, confirmNewSpellings, markNewSpellings,
} from "../api.js";
import { collectNewIds } from "./glossaryNew.js";
import { orderGlossaryEntries } from "./glossaryOrder.js";

const deleteBtnClass =
  "inline-flex h-6 w-6 items-center justify-center text-muted-foreground leading-none " +
  "transition-all hover:text-destructive active:scale-90 focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100";

// 용어집 표기 셀 — 클릭하면 입력창으로 바뀌고, blur 시 값이 바뀌었을 때만
// PATCH를 보낸다(불필요한 요청 방지).
function GlossarySpellingCell({ entry, columnKey, onSaved, onError, onConfirmNew }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.spellings[columnKey] || "");
  const newId = entry.new?.[columnKey];

  if (!editing) {
    return (
      <span
        className="-mx-1.5 -my-0.5 flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-0.5 text-foreground transition-colors hover:bg-accent/60"
        onClick={() => setEditing(true)}
      >
        {newId && (
          <button
            type="button"
            aria-label="새로 추가된 표기 확인"
            onClick={(e) => {
              e.stopPropagation();
              onConfirmNew([newId]);
            }}
            className="h-2 w-2 shrink-0 rounded-full bg-primary"
          />
        )}
        {entry.spellings[columnKey] || <span className="text-muted-foreground">—</span>}
      </span>
    );
  }
  const candidates = entry.candidates?.[columnKey] ?? [];
  const listId = `cand-${entry.id}-${columnKey}`;
  return (
    <>
    {candidates.length > 0 && (
      <datalist id={listId}>
        {candidates.map((c) => <option key={c} value={c} />)}
      </datalist>
    )}
    <input
      list={candidates.length > 0 ? listId : undefined}
      className="w-full rounded border border-input bg-background px-1 py-0.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={async () => {
        setEditing(false);
        if (value !== (entry.spellings[columnKey] || "")) {
          try {
            await patchGlossaryEntry(entry.id, { spellings: { [columnKey]: value } });
            onSaved();
          } catch (err) {
            onError?.(err.message ?? "표기 저장 중 오류가 발생했습니다.");
          }
        }
      }}
    />
    </>
  );
}

// 작품 목록(설정) 화면과 검수 작업 화면이 같은 용어집 데이터를 같은
// 엔드포인트(POST/PATCH/DELETE /glossary)로 다루므로, 추가/수정/삭제
// 로직을 여기 하나로 모아 두 화면이 항상 같은 방식으로 DB에 반영하게 한다
// — 화면별로 따로 구현하면 한쪽만 검증/에러 처리가 달라지는 어긋남이
// 생기기 쉽다.
export default function GlossaryTable({ titleId, entries, columns, onChanged, onError }) {
  const [newTerm, setNewTerm] = useState("");
  const [newTermError, setNewTermError] = useState(null);
  const { pending, rest } = orderGlossaryEntries(entries, columns);
  const newIds = collectNewIds(entries);
  // 직전에 "확인 완료"한 칸들 — 실수로 눌렀을 때 "다시 표시"로 되돌린다. 화면을
  // 열어 둔 동안만 유지된다(새로고침하면 사라짐).
  const [justConfirmed, setJustConfirmed] = useState([]);

  async function confirmNew(ids) {
    try {
      await confirmNewSpellings(ids);
      setJustConfirmed(ids);
      onChanged();
    } catch (err) {
      onError?.(err.message ?? "신규 표시 확인 중 오류가 발생했습니다.");
    }
  }

  async function restoreNew() {
    try {
      await markNewSpellings(justConfirmed);
      setJustConfirmed([]);
      onChanged();
    } catch (err) {
      onError?.(err.message ?? "신규 표시 복원 중 오류가 발생했습니다.");
    }
  }

  async function submitNewTerm() {
    const koreanTerm = newTerm.trim();
    if (!koreanTerm) return;
    setNewTermError(null);
    try {
      await postGlossaryEntry(titleId, { korean_term: koreanTerm, category: "person", aliases: [] });
      setNewTerm("");
      onChanged();
    } catch (err) {
      setNewTermError(err.message ?? "용어 추가 중 오류가 발생했습니다.");
    }
  }

  async function handleDelete(entryId) {
    if (!window.confirm("이 용어를 삭제할까요?")) return;
    try {
      await deleteGlossaryEntry(entryId);
      onChanged();
    } catch (err) {
      onError?.(err.message ?? "용어 삭제 중 오류가 발생했습니다.");
    }
  }

  return (
    <div className="overflow-x-auto">
      {(newIds.length > 0 || justConfirmed.length > 0) && (
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          {newIds.length > 0 && (
            <button
              type="button"
              onClick={() => confirmNew(newIds)}
              className="inline-flex items-center gap-1.5 rounded border border-input px-2 py-1 text-foreground hover:bg-accent/60"
            >
              <span className="h-2 w-2 rounded-full bg-primary" />
              {newIds.length}건 확인 완료
            </button>
          )}
          {justConfirmed.length > 0 && (
            <button
              type="button"
              onClick={restoreNew}
              className="rounded border border-input px-2 py-1 text-foreground hover:bg-accent/60"
            >
              다시 표시
            </button>
          )}
        </div>
      )}
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="divide-x divide-border/30 border-b border-border bg-card text-foreground">
            <th className="sticky left-0 z-10 bg-card px-4 py-2 text-left font-bold">한국어 용어</th>
            {columns.map((col) => (
              <th key={col} className="px-4 py-2 text-left font-bold">
                {col}
              </th>
            ))}
            <th className="w-8"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {pending.length > 0 && (
            <tr>
              <td className="bg-muted/50 px-4 py-1.5 text-muted-foreground" colSpan={columns.length + 2}>
                표기를 정해야 하는 금액 {pending.length}건
              </td>
            </tr>
          )}
          {[...pending, ...rest].map((entry) => (
            <tr key={entry.id} className="group divide-x divide-border/30 even:bg-muted/30 hover:bg-accent/40">
              <td className="sticky left-0 z-10 bg-card px-4 py-2 font-medium text-foreground group-even:bg-muted/30 group-hover:bg-accent/40">
                {entry.korean_term}
                {entry.category === "amount" && (
                  <span className="ml-1.5 rounded bg-muted px-1 text-[10px] font-normal text-muted-foreground">금액</span>
                )}
              </td>
              {columns.map((col) => (
                <td key={col} className="px-4 py-2">
                  <GlossarySpellingCell entry={entry} columnKey={col} onSaved={onChanged} onError={onError} onConfirmNew={confirmNew} />
                </td>
              ))}
              <td className="px-4 py-2">
                <button
                  type="button"
                  aria-label={`${entry.korean_term} 삭제`}
                  onClick={() => handleDelete(entry.id)}
                  className={deleteBtnClass}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
          <tr>
            <td className="px-4 py-2" colSpan={columns.length + 2}>
              <input
                type="text"
                value={newTerm}
                onChange={(e) => {
                  setNewTerm(e.target.value);
                  setNewTermError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitNewTerm();
                }}
                placeholder="+ 새 용어 입력 후 Enter"
                className="w-full bg-transparent text-foreground placeholder:text-muted-foreground focus-visible:outline-none"
              />
              {newTermError && (
                <p role="status" aria-live="polite" className="mt-1 text-xs text-destructive">
                  {newTermError}
                </p>
              )}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
