import { useState } from "react";

// 브라우저 기본 <details>는 open/close를 순간적으로 처리해 transition이
// 안 먹어서, open 상태를 직접 관리하고 grid-template-rows를 0fr<->1fr로
// 움직이는 방식으로 부드럽게 펼쳐지게 한다.
export default function Disclosure({ summary, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-1.5 border-b border-border bg-muted px-4 py-3 text-left text-xs font-semibold text-foreground"
      >
        <span>{summary}</span>
        <svg
          viewBox="0 0 20 20"
          fill="currentColor"
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        >
          <path
            fillRule="evenodd"
            d="M6 4a1 1 0 0 1 1.7-.7l5 5a1 1 0 0 1 0 1.4l-5 5A1 1 0 0 1 6 14V4Z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      <div
        className={`grid transition-[grid-template-rows] duration-200 ease-out ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">{children}</div>
      </div>
    </div>
  );
}
