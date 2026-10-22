// 용어집 목록에서 신규 표시가 켜진 모든 표기 칸의 id를 모은다 — "확인 완료"가
// 한 번에 끄고, "다시 표시"가 같은 id들을 다시 켠다. entry.new는 {열 키: spelling id}.
export function collectNewIds(entries) {
  return entries.flatMap((entry) => Object.values(entry.new ?? {}));
}
