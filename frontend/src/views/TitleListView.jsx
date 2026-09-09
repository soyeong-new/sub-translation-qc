// 작품 목록/등록 화면의 얇은 껍데기 — 실제 목록/상세/등록 폼은 전부
// TitleArchiveList가 그린다(마스터-디테일 레이아웃).

import QQLogo from "../components/QQLogo.jsx";
import TitleArchiveList from "./TitleArchiveList.jsx";

export default function TitleListView({ onSelect }) {
  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center border-b border-border/50 bg-card/90 px-6 backdrop-blur">
        <div className="flex items-center gap-2.5">
          <QQLogo className="h-6 w-auto" />
          <span className="text-sm font-semibold tracking-tight text-foreground">Subtitle QC</span>
        </div>
      </header>
      <main className="mx-auto flex h-[calc(100vh-56px)] w-full max-w-6xl gap-4 px-4 py-5">
        <TitleArchiveList onOpen={onSelect} />
      </main>
    </div>
  );
}
