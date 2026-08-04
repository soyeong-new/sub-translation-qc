// 작품 목록/등록, 인물관계도 검토, 리뷰 화면 사이를 전환하는 최상위 라우팅 컴포넌트.
// 라우터 라이브러리 없이 useState 기반 화면 전환을 그대로 확장한다.

import { useState } from "react";
import TitleListView from "./views/TitleListView.jsx";
import ReviewView from "./views/ReviewView.jsx";
import ChartReviewView from "./views/ChartReviewView.jsx";

export default function App() {
  const [screen, setScreen] = useState({ name: "titles" });

  if (screen.name === "review") {
    return (
      <ReviewView
        targetVersionId={screen.targetVersionId}
        onBack={() => setScreen({ name: "titles" })}
        onOpenChart={(titleId) => setScreen({ name: "chart", titleId })}
      />
    );
  }

  if (screen.name === "chart") {
    return (
      <ChartReviewView
        titleId={screen.titleId}
        onBack={() => setScreen({ name: "titles" })}
      />
    );
  }

  return (
    <TitleListView onSelect={(targetVersionId) => setScreen({ name: "review", targetVersionId })} />
  );
}
