// 작품 목록/등록, 리뷰 화면 사이를 전환하는 최상위 라우팅 컴포넌트.
// 라우터 라이브러리 없이 useState 기반 화면 전환을 그대로 확장한다.

import { useEffect, useState } from "react";
import TitleListView from "./views/TitleListView.jsx";
import ReviewView from "./views/ReviewView.jsx";
import RegisterConfirmationView from "./views/RegisterConfirmationView.jsx";

// 새로고침해도 "분석 다 끝난 target_version"으로 이어지도록 현재 화면을
// localStorage에 저장해둔다 — STT/AI 검증 결과는 이미 서버 DB에 있으니,
// 프론트가 잃어버리는 건 "지금 몇 번 화면에 있었는지"뿐이다.
const STORAGE_KEY = "qc_screen";

function loadScreen() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : { name: "titles" };
  } catch {
    return { name: "titles" };
  }
}

export default function App() {
  const [screen, setScreen] = useState(loadScreen);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(screen));
  }, [screen]);

  if (screen.name === "confirm") {
    return (
      <RegisterConfirmationView
        targetVersionId={screen.targetVersionId}
        onDone={() =>
          setScreen({ name: "review", targetVersionId: screen.targetVersionId, titleId: screen.titleId })
        }
        onExit={() => setScreen({ name: "titles" })}
      />
    );
  }

  if (screen.name === "review") {
    return (
      <ReviewView
        targetVersionId={screen.targetVersionId}
        titleId={screen.titleId}
        onBack={() => setScreen({ name: "titles" })}
      />
    );
  }

  // TitleListView는 분석이 "review"(확인 필요 없음)로 끝났는지 "awaiting_confirmation"
  // (성별/격식 확인 필요)로 끝났는지 status를 함께 넘긴다 — 후자는 findings
  // 화면으로 바로 가지 않고 확인 페이지를 먼저 거친다.
  return (
    <TitleListView
      onSelect={(targetVersionId, status, titleId) =>
        setScreen({
          name: status === "awaiting_confirmation" ? "confirm" : "review",
          targetVersionId,
          titleId,
        })
      }
    />
  );
}
