// 모든 화면 공통의 최상단 헤더 — 로고 위치를 화면마다 다시 짜지 않도록 공용화.
import QQLogo from "./QQLogo.jsx";

export default function AppHeader() {
  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center border-b border-border/50 bg-card/90 px-6 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <QQLogo className="h-6 w-auto" />
        <span className="text-sm font-semibold tracking-tight text-foreground">Subtitle QC</span>
      </div>
    </header>
  );
}
