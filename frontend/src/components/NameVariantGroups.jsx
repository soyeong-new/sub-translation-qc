import { useState } from "react";
import { planNameVariantChange } from "./nameVariants.js";

// 같은 이름이 철자만 달리 쓰인 묶음마다 한 줄 — 쓸 표기를 고르고 [적용]을 누르면 체크된 줄이 그 표기로
// 바뀐다. 줄 목록과 용어집 등록은 접어 둔다(평소엔 안 쓰는 기능).
function NameVariantGroup({ group, busy, timeOf, onApply, onDismiss }) {
  const [chosen, setChosen] = useState(group.recommended);
  const [open, setOpen] = useState(false);
  const [skipped, setSkipped] = useState({});
  const [register, setRegister] = useState(false);
  const [koreanTerm, setKoreanTerm] = useState(group.suggested_korean_term ?? "");

  const rows = planNameVariantChange(group, chosen);
  const selected = rows.filter((r) => !skipped[r.line.segment_id]);
  const canRegister = !group.has_registered;
  const registerTerm = register && canRegister ? koreanTerm.trim() : "";

  return (
    <li className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-3 px-3 py-2.5">
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="쓸 표기">
          {group.variants.map((v) => (
            <button
              key={v.surface}
              type="button"
              role="radio"
              aria-checked={v.surface === chosen}
              onClick={() => setChosen(v.surface)}
              className={`rounded-full border px-2.5 py-0.5 text-sm transition-colors ${
                v.surface === chosen
                  ? "border-primary/40 bg-primary/10 font-semibold text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {v.surface}
              <span className="ml-1 text-[11px] font-normal opacity-70">
                {v.count}{v.registered ? " · 용어집" : ""}
              </span>
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" onClick={onDismiss} className="text-xs text-muted-foreground hover:text-foreground">
            다른 이름
          </button>
          <button
            type="button"
            disabled={busy || selected.length === 0 || (register && canRegister && !registerTerm)}
            onClick={() => onApply({
              items: selected.map((r) => ({ segment_id: r.line.segment_id, replacements: r.replacements })),
              surface: chosen,
              koreanTerm: registerTerm,
            })}
            className="rounded-md bg-success px-3 py-1 text-xs font-semibold text-success-foreground hover:bg-success/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            적용
          </button>
        </div>
      </div>
      <div className="flex items-center justify-between px-3 pb-2 text-xs text-muted-foreground">
        <span>바뀌는 줄 {rows.length}개</span>
        <button type="button" onClick={() => setOpen((v) => !v)} className="underline decoration-dotted hover:text-foreground">
          {open ? "접기 ▴" : "줄 보기 ▾"}
        </button>
      </div>
      {open && (
        <div className="space-y-1 border-t border-border bg-muted/30 px-3 py-2 text-xs">
          {rows.map(({ line, replacements }) => (
            <label key={line.segment_id} className="flex items-start gap-2 py-0.5">
              <input
                type="checkbox"
                checked={!skipped[line.segment_id]}
                onChange={() => setSkipped((p) => ({ ...p, [line.segment_id]: !p[line.segment_id] }))}
                className="mt-0.5"
              />
              <span className="w-16 shrink-0 text-[11px] text-muted-foreground">{timeOf(line.segment_id)}</span>
              <span className="min-w-0 whitespace-pre-line text-foreground">
                {line.text}
                <span className="ml-1.5 text-muted-foreground">
                  ({replacements.map((r) => `${r.from_text} → ${r.to_text}`).join(", ")})
                </span>
              </span>
            </label>
          ))}
          {canRegister && (
            <div className="flex flex-wrap items-center gap-2 pt-1.5 text-muted-foreground">
              <label className="flex items-center gap-1.5 text-foreground">
                <input type="checkbox" checked={register} onChange={(e) => setRegister(e.target.checked)} />
                용어집에도 등록
              </label>
              {register && (
                <>
                  <span>한국어</span>
                  <input
                    type="text"
                    value={koreanTerm}
                    onChange={(e) => setKoreanTerm(e.target.value)}
                    className="w-24 rounded border border-input bg-background px-1.5 py-0.5 text-foreground"
                  />
                  <span>(인물)</span>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export default function NameVariantGroups({ groups, error, ...rest }) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-foreground">
        같은 이름, 다른 표기 <span className="font-normal text-muted-foreground">· 쓸 표기를 고르세요</span>
      </p>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <ul className="space-y-2">
        {groups.map((g) => (
          <NameVariantGroup key={g.id} group={g} {...rest} onDismiss={() => rest.onDismiss(g.id)} />
        ))}
      </ul>
    </div>
  );
}
