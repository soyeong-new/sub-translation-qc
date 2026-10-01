// 화면별 기능 안내 투어 — driver.js로 화면 요소를 직접 짚어가며 설명한다.
// 로그인 계정이 없는 공용 툴이라 "처음 왔는지"는 이 브라우저(컴퓨터) 기준
// localStorage로만 구분할 수 있다.
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import "./tour.css";

export function runTour(steps, options = {}) {
  driver({
    showProgress: true,
    progressText: "{{current}} / {{total}}",
    nextBtnText: "다음",
    prevBtnText: "이전",
    doneBtnText: "완료",
    // 대상 요소가 조건부로 렌더링돼 아직 DOM에 없을 수 있어(예: 분할 버튼),
    // 나타날 때까지 잠깐 기다렸다가 짚어준다.
    waitForElement: 1000,
    steps,
    ...options,
  }).drive();
}

// 새 기능이 생겨 투어 내용을 바꿀 때는 key 뒤에 버전을 올린다(예: "review-v2")
// — 이미 한 번 본 사람에게도 바뀐 안내가 다시 한 번 뜨게 하기 위해서다.
export function autoRunTour(key, steps, options = {}) {
  const seenKey = `qc_tour_seen:${key}`;
  if (localStorage.getItem(seenKey)) return;
  localStorage.setItem(seenKey, "1");
  runTour(steps, options);
}
