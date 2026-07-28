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
