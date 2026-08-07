// 작품 목록/등록, 리뷰 화면 사이를 전환하는 최상위 라우팅 컴포넌트.
// 라우터 라이브러리 없이 useState 기반 화면 전환을 그대로 확장한다.

import { useState } from "react";
import TitleListView from "./views/TitleListView.jsx";
import ReviewView from "./views/ReviewView.jsx";

export default function App() {
  const [screen, setScreen] = useState({ name: "titles" });

  if (screen.name === "review") {
    return (
      <ReviewView
        targetVersionId={screen.targetVersionId}
        onBack={() => setScreen({ name: "titles" })}
      />
    );
  }

  return (
    <TitleListView onSelect={(targetVersionId) => setScreen({ name: "review", targetVersionId })} />
  );
}
