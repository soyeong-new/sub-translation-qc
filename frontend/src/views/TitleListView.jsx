// 작품 목록/등록 화면의 얇은 껍데기 — 실제 목록/상세/등록 폼은 전부
// TitleArchiveList가 그린다(마스터-디테일 레이아웃).

import AppHeader from "../components/AppHeader.jsx";
import { runTour } from "../tour.js";
import TitleArchiveList, { TITLE_LIST_TOUR_STEPS } from "./TitleArchiveList.jsx";

export default function TitleListView({ onSelect }) {
  return (
    <div className="flex h-screen flex-col bg-background">
      <AppHeader />
      <div className="px-6 pt-1.5">
        <button
          type="button"
          onClick={() => runTour(TITLE_LIST_TOUR_STEPS)}
          className="text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          튜토리얼 보기
        </button>
      </div>
      <main className="mx-auto flex h-[calc(100vh-56px)] w-full max-w-6xl gap-4 px-4 py-5">
        <TitleArchiveList onOpen={onSelect} />
      </main>
    </div>
  );
}
