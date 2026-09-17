import { useState } from "react";
import { postGlossaryEntry, patchGlossaryEntry, deleteGlossaryEntry } from "../api.js";

const deleteBtnClass =
  "inline-flex h-6 w-6 items-center justify-center text-muted-foreground leading-none " +
  "transition-all hover:text-destructive active:scale-90 focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100";

// 용어집 표기 셀 — 클릭하면 입력창으로 바뀌고, blur 시 값이 바뀌었을 때만
// PATCH를 보낸다(불필요한 요청 방지).
function GlossarySpellingCell({ entry, columnKey, onSaved, onError }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.spellings[columnKey] || "");

  if (!editing) {
    return (
      <span
        className="-mx-1.5 -my-0.5 block cursor-pointer rounded px-1.5 py-0.5 text-foreground transition-colors hover:bg-accent/60"
        onClick={() => setEditing(true)}
      >
        {entry.spellings[columnKey] || <span className="text-muted-foreground">—</span>}
      </span>
    );
  }
  return (
    <input
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
          {[...entries].sort((a, b) => a.korean_term.localeCompare(b.korean_term, "ko")).map((entry) => (
            <tr key={entry.id} className="group divide-x divide-border/30 even:bg-muted/30 hover:bg-accent/40">
              <td className="sticky left-0 z-10 bg-card px-4 py-2 font-medium text-foreground group-even:bg-muted/30 group-hover:bg-accent/40">
                {entry.korean_term}
              </td>
              {columns.map((col) => (
                <td key={col} className="px-4 py-2">
                  <GlossarySpellingCell entry={entry} columnKey={col} onSaved={onChanged} onError={onError} />
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
