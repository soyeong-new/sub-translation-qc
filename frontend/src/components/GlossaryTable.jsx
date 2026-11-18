import { useState } from "react";
import {
  postGlossaryEntry, patchGlossaryEntry, deleteGlossaryEntry, confirmNewSpellings, markNewSpellings,
} from "../api.js";
import { collectNewIds } from "./glossaryNew.js";
import { orderGlossaryEntries } from "./glossaryOrder.js";
import { findSpellingConflicts, planUnify } from "./glossarySpelling.js";

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

  // 같은 이름이 철자만 달리 섞여 있으면 고른 표기로 그 부분만 통일한다.
  // 직전 통일은 화면을 열어 둔 동안 되돌릴 수 있다.
  const [dismissed, setDismissed] = useState([]); // "이대로 두기"한 충돌 — 화면을 연 동안만 기억
  const conflicts = findSpellingConflicts(entries, columns).filter((c) => !dismissed.includes(`${c.column}:${c.key}`));
  const [lastUnify, setLastUnify] = useState([]);
  const [unifyOpen, setUnifyOpen] = useState(false);
  const [picked, setPicked] = useState({}); // 충돌 키 → 고른 표기
  const [unchecked, setUnchecked] = useState([]); // 이번에 바꾸지 않을 항목 id

  async function saveSpellings(items, field) {
    try {
      for (const it of items) {
        await patchGlossaryEntry(it.entryId, { spellings: { [it.column]: it[field] } });
      }
    } catch (err) {
      onError?.(err.message ?? "표기 통일 중 오류가 발생했습니다.");
    }
    onChanged();
  }

  const conflictId = (c) => `${c.column}:${c.key}`;
  // 아직 고르지 않았으면 가장 많은 항목이 쓰는 표기가 기본값이다.
  const pickedSurface = (c) => picked[conflictId(c)]
    ?? [...c.variants].sort((a, b) => b.entryIds.length - a.entryIds.length)[0].surface;
  const entryById = new Map(entries.map((e) => [e.id, e]));
  const categoryLabel = { person: "인물", place: "장소", business: "상호", title: "작품명" };

  async function unify(conflict) {
    const plan = planUnify(entries, conflict, pickedSurface(conflict)).filter((p) => !unchecked.includes(p.entryId));
    setLastUnify(plan);
    await saveSpellings(plan, "after");
  }

  async function undoUnify() {
    const plan = lastUnify;
    setLastUnify([]);
    await saveSpellings(plan, "before");
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
      {conflicts.length > 0 && (
        <div className="mb-2 flex items-center justify-between rounded border border-input bg-card px-3 py-1.5 text-xs">
          <span className="text-muted-foreground">같은 이름의 표기가 섞여 있습니다 ({conflicts.length}건)</span>
          <button
            type="button"
            onClick={() => setUnifyOpen(true)}
            className="rounded border border-input px-2 py-0.5 font-semibold text-foreground hover:bg-accent/60"
          >
            확인하기
          </button>
        </div>
      )}
      {unifyOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4">
          <div className="flex max-h-[88vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card text-xs shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-3 text-sm font-bold">
              표기 통일
              <button type="button" aria-label="닫기" onClick={() => { setUnifyOpen(false); setLastUnify([]); }}
                className="text-muted-foreground hover:text-foreground">✕</button>
            </div>
            <div className="space-y-4 overflow-y-auto px-4 py-3">
              {conflicts.map((c) => {
                const surface = pickedSurface(c);
                const plan = planUnify(entries, c, surface);
                const changing = new Map(plan.map((p) => [p.entryId, p]));
                const checkedCount = plan.filter((p) => !unchecked.includes(p.entryId)).length;
                const affected = [...new Set(c.variants.flatMap((v) => v.entryIds))];
                return (
                  <div key={conflictId(c)} className="space-y-2">
                    <div className="text-muted-foreground">{c.column} · 같은 이름이 다르게 등록되어 있습니다. 쓸 표기를 고르세요</div>
                    <div className="flex flex-wrap gap-1.5">
                      {c.variants.map((v) => (
                        <button
                          key={v.surface}
                          type="button"
                          onClick={() => setPicked({ ...picked, [conflictId(c)]: v.surface })}
                          className={"rounded-full border px-2.5 py-0.5 " + (v.surface === surface
                            ? "border-primary bg-primary/10 font-bold text-primary"
                            : "border-transparent text-muted-foreground hover:bg-accent/60")}
                        >
                          {v.surface} <span className="opacity-60">{v.entryIds.length}개 항목</span>
                        </button>
                      ))}
                    </div>
                    <div className="text-muted-foreground">바뀌는 항목 {checkedCount}개</div>
                    <div className="divide-y divide-border/40">
                      {affected.map((id) => {
                        const e = entryById.get(id);
                        const change = changing.get(id);
                        return (
                          <div key={id} className="flex items-baseline gap-2 py-1.5">
                            {change ? (
                              <input
                                type="checkbox"
                                checked={!unchecked.includes(id)}
                                onChange={() => setUnchecked(unchecked.includes(id)
                                  ? unchecked.filter((x) => x !== id) : [...unchecked, id])}
                              />
                            ) : <span className="w-3.5" />}
                            <span className="w-28 shrink-0 font-semibold text-foreground">
                              {e.korean_term}
                              <span className="ml-1 font-normal text-muted-foreground">{categoryLabel[e.category] ?? ""}</span>
                            </span>
                            {change ? (
                              <span>
                                <del className="text-muted-foreground">{change.before}</del>
                                {" → "}<span className="rounded bg-green-100 px-1 font-semibold text-green-800">{change.after}</span>
                              </span>
                            ) : (
                              <span className="text-muted-foreground">{e.spellings?.[c.column]} (그대로)</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex items-center justify-end gap-3">
                      <button
                        type="button"
                        onClick={() => setDismissed([...dismissed, conflictId(c)])}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        이대로 두기
                      </button>
                      <button
                        type="button"
                        disabled={checkedCount === 0}
                        onClick={() => unify(c)}
                        className="rounded bg-green-700 px-3 py-1 font-bold text-white hover:bg-green-800 disabled:opacity-50"
                      >
                        적용
                      </button>
                    </div>
                  </div>
                );
              })}
              {conflicts.length === 0 && <div className="text-muted-foreground">섞인 표기가 없습니다.</div>}
              {lastUnify.length > 0 && (
                <div className="flex items-center justify-between rounded-lg bg-muted px-3 py-2 text-muted-foreground">
                  {lastUnify.length}개 항목을 통일했습니다.
                  <button type="button" onClick={undoUnify}
                    className="rounded border border-input bg-card px-2 py-0.5 text-foreground hover:bg-accent/60">
                    되돌리기
                  </button>
                </div>
              )}
            </div>
            <div className="flex justify-end border-t border-border px-4 py-2.5">
              <button type="button" onClick={() => { setUnifyOpen(false); setLastUnify([]); }}
                className="rounded border border-input px-3 py-1 font-semibold text-foreground hover:bg-accent/60">
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="divide-x divide-border/30 border-b border-border bg-card text-foreground">
            <th className="sticky left-0 z-10 bg-card px-4 py-2 text-left font-bold">한국어 용어</th>
            {columns.map((col) => (
              <th key={col} className="min-w-[11rem] px-4 py-2 text-left font-bold">
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
