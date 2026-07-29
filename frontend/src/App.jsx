// 작품 목록/등록 화면과 리뷰 화면 사이를 전환하는 최상위 라우팅 컴포넌트.

import { useState } from "react";
import TitleListView from "./views/TitleListView.jsx";
import ReviewView from "./views/ReviewView.jsx";

export default function App() {
  const [selectedTargetVersionId, setSelectedTargetVersionId] = useState(null);

  return selectedTargetVersionId ? (
    <ReviewView
      targetVersionId={selectedTargetVersionId}
      onBack={() => setSelectedTargetVersionId(null)}
    />
  ) : (
    <TitleListView onSelect={setSelectedTargetVersionId} />
  );
}
