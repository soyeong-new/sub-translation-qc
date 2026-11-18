// 표기 통일 카드의 계산 — 고른 표기(chosen)로 바꿀 줄과 줄별 바꿈 쌍.
const foldFirst = (s) => s.slice(0, 1).toLowerCase() + s.slice(1);

// 원래 자리의 첫 글자 대소문자를 따른다: 문장 앞이라 대문자였으면 소문자 표기도 대문자로 시작한다.
export function matchFirstCase(from, to) {
  if (from[0] !== from[0].toLowerCase() && to[0] === to[0].toLowerCase() && to[0] !== to[0].toUpperCase()) {
    return to[0].toUpperCase() + to.slice(1);
  }
  return to;
}

// chosen이 아닌 표기가 든 줄과 그 줄의 [{from_text, to_text}] — 첫 글자 대소문자만 다른 표기는 같은 표기다.
export function planNameVariantChange(group, chosen) {
  const key = foldFirst(chosen);
  const rows = [];
  for (const line of group.lines) {
    const others = line.surfaces.filter((sf) => foldFirst(sf) !== key);
    if (others.length) {
      rows.push({
        line,
        replacements: others.map((sf) => ({ from_text: sf, to_text: matchFirstCase(sf, chosen) })),
      });
    }
  }
  return rows;
}
