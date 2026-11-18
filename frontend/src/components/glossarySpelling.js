// 같은 이름이 용어집 여러 항목에 철자만 달리 등록된 경우("Geum Jan-Di" / "Pressing Jan Di")를
// 찾아 한 가지로 통일한다. 단어는 공백·하이픈 기준으로 나누고, 연속된 단어 묶음을
// 글자만 소문자로 이은 키("jandi")로 비교한다. 첫 글자의 대소문자 차이는 같은 표기로
// 본다(문장 앞이라 대문자가 된 경우) — 안쪽 대소문자(Jan-di / Jan-Di)는 다른 표기다.
const MAX_WORDS = 6;
const MIN_KEY_LENGTH = 3;

const keyOf = (surface) => surface.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
const foldFirst = (surface) => surface[0].toLowerCase() + surface.slice(1);

// 연속된 1~MAX_WORDS 단어 묶음마다 {key, surface, start, end}.
function* spans(text) {
  const words = [...text.matchAll(/[^\s-]+/g)].map((m) => ({ start: m.index, end: m.index + m[0].length }));
  for (let i = 0; i < words.length; i++) {
    for (let j = i; j < Math.min(words.length, i + MAX_WORDS); j++) {
      const start = words[i].start;
      const end = words[j].end;
      const surface = text.slice(start, end);
      const key = keyOf(surface);
      if (key.length >= MIN_KEY_LENGTH) yield { key, surface, start, end };
    }
  }
}

// [{column, key, variants: [{surface, entryIds}]}] — 금액 항목은 비교하지 않는다.
export function findSpellingConflicts(entries, columns) {
  const conflicts = [];
  for (const column of columns) {
    const byKey = new Map(); // key → Map(첫 글자 접은 표기 → {surface, entryIds})
    for (const entry of entries) {
      const text = entry.spellings?.[column];
      if (!text || entry.category === "amount") continue;
      for (const { key, surface } of spans(text)) {
        if (!byKey.has(key)) byKey.set(key, new Map());
        const variants = byKey.get(key);
        const folded = foldFirst(surface);
        if (!variants.has(folded)) variants.set(folded, { surface, entryIds: new Set() });
        variants.get(folded).entryIds.add(entry.id);
      }
    }
    const found = [];
    for (const [key, variants] of byKey) {
      if (variants.size < 2) continue;
      const list = [...variants.values()].map((v) => ({ surface: v.surface, entryIds: [...v.entryIds] }));
      if (new Set(list.flatMap((v) => v.entryIds)).size < 2) continue;
      found.push({ column, key, variants: list });
    }
    // 더 짧은 키의 충돌이 같은 항목들을 이미 설명하면(Jan-Di ⊂ Pressing Jan-Di) 긴 쪽은 뺀다.
    const ids = (c) => new Set(c.variants.flatMap((v) => v.entryIds));
    for (const c of found) {
      const covered = found.some((d) => d.key.length < c.key.length && c.key.includes(d.key)
        && [...ids(c)].every((id) => ids(d).has(id)));
      if (!covered) conflicts.push(c);
    }
  }
  return conflicts;
}

// "이대로 두기" 기억용 식별자 — 언어 칸 + 표기들(첫 글자 접음, 정렬). 같은 이름에 새 표기가 생기면 달라져서 다시 묻는다.
export const conflictKey = (c) => [c.column, ...c.variants.map((v) => foldFirst(v.surface)).sort()].join("|");

// text 안에서 key에 해당하는 부분을 surface로 바꾼다. 원래 자리의 첫 글자 대소문자는 유지.
export function unifySpelling(text, key, surface) {
  let out = "";
  let last = 0;
  for (const s of spans(text)) {
    if (s.key !== key || s.start < last) continue;
    const first = text[s.start];
    const isUpper = first !== first.toLowerCase();
    const isLower = first !== first.toUpperCase();
    const head = isUpper ? surface[0].toUpperCase() : isLower ? surface[0].toLowerCase() : surface[0];
    out += text.slice(last, s.start) + head + surface.slice(1);
    last = s.end;
  }
  return out + text.slice(last);
}

// 선택한 표기로 통일했을 때 바뀌는 항목들 — [{entryId, column, before, after}].
export function planUnify(entries, conflict, surface) {
  const plan = [];
  for (const entry of entries) {
    const before = entry.spellings?.[conflict.column];
    if (!before || entry.category === "amount") continue;
    const after = unifySpelling(before, conflict.key, surface);
    if (after !== before) plan.push({ entryId: entry.id, column: conflict.column, before, after });
  }
  return plan;
}
