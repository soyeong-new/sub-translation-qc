import { useState } from "react";

// 숨긴 충돌 목록 — 잘못 눌렀을 때 "다시 보기"로 되살린다. 없으면 아무것도 그리지 않는다.
export default function DismissedConflicts({ items, onRestore }) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className="mb-2 px-4 pt-2 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="text-muted-foreground underline decoration-dotted hover:text-foreground">
        숨긴 항목 {items.length}건 {open ? "▴" : "▾"}
      </button>
      {open && (
        <div className="mt-1 divide-y divide-border/40 rounded border border-input bg-card px-3">
          {items.map((i) => (
            <div key={i.id} className="flex items-center justify-between py-1.5">
              <span className="text-foreground">{i.key.split("|").join(" / ")}</span>
              <button type="button" onClick={() => onRestore(i.id)}
                className="rounded border border-input px-2 py-0.5 text-foreground hover:bg-accent/60">
                다시 보기
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
