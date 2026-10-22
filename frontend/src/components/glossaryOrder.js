// 표기가 아직 비어 있는 금액(amount) 항목은 사용자가 채워야 효과가 생기므로
// 맨 위에 모아 보여 주고, 나머지는 기존처럼 한국어 가나다순으로 둔다. 표기를
// 채우면 그 항목은 pending에서 빠져 다음 그릴 때 가나다순 자리로 내려간다.
const byKoreanTerm = (a, b) => a.korean_term.localeCompare(b.korean_term, "ko");

export function orderGlossaryEntries(entries, columns) {
  const isPending = (entry) =>
    entry.category === "amount" && columns.some((col) => !entry.spellings[col]);
  const pending = entries.filter(isPending).sort(byKoreanTerm);
  const rest = entries.filter((entry) => !isPending(entry)).sort(byKoreanTerm);
  return { pending, rest };
}
