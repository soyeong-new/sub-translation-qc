import test from "node:test";
import assert from "node:assert/strict";
import { findSpellingConflicts, unifySpelling, planUnify, conflictKey } from "./glossarySpelling.js";

const e = (id, spellings, category = "person") => ({ id, korean_term: id, category, spellings });
const COL = ["fr_FR"];

test("spacing and hyphen variants of the same name are one conflict", () => {
  const entries = [e("금잔디", { fr_FR: "Geum Jan-Di" }), e("잔디 세탁", { fr_FR: "Pressing Jan Di" })];
  const found = findSpellingConflicts(entries, COL);
  assert.equal(found.length, 1);
  assert.equal(found[0].key, "jandi");
  assert.deepEqual(found[0].variants.map((v) => v.surface).sort(), ["Jan Di", "Jan-Di"]);
});

test("one word vs two words", () => {
  const entries = [e("신화", { fr_FR: "Shinhwa" }), e("신화 학원", { fr_FR: "Académie Shin Hwa" })];
  assert.equal(findSpellingConflicts(entries, COL)[0].key, "shinhwa");
});

test("first-letter case alone is not a conflict, inner case is", () => {
  const same = [e("a", { fr_FR: "madame la présidente" }), e("b", { fr_FR: "Madame la présidente" })];
  assert.deepEqual(findSpellingConflicts(same, COL), []);
  const inner = [e("a", { fr_FR: "Geum Jan-Di" }), e("b", { fr_FR: "Pressing Jan-di" })];
  assert.equal(findSpellingConflicts(inner, COL)[0].key, "jandi");
});

test("consistent glossary, amounts and single entries give no conflict", () => {
  const entries = [e("a", { fr_FR: "Jan-Di" }), e("b", { fr_FR: "Pressing Jan-Di" }),
    e("10억", { fr_FR: "1 milliard" }, "amount"), e("100억", { fr_FR: "1 Milliard" }, "amount")];
  assert.deepEqual(findSpellingConflicts(entries, COL), []);
});

test("longer key already explained by a shorter one is dropped", () => {
  const entries = [e("a", { fr_FR: "Pressing Jan-Di" }), e("b", { fr_FR: "Pressing Jan Di" })];
  const found = findSpellingConflicts(entries, COL);
  assert.deepEqual(found.map((c) => c.key), ["jandi"]);
});

test("unify replaces only the name part and keeps the first-letter case", () => {
  assert.equal(unifySpelling("Pressing Jan Di", "jandi", "Jan-Di"), "Pressing Jan-Di");
  assert.equal(unifySpelling("Shin Hwa", "shinhwa", "shinhwa"), "Shinhwa");
  assert.equal(unifySpelling("lycée Shin Hwa et Shin Hwa", "shinhwa", "Shinhwa"), "lycée Shinhwa et Shinhwa");
});

test("planUnify lists only entries that change", () => {
  const entries = [e("금잔디", { fr_FR: "Geum Jan-Di" }), e("잔디 세탁", { fr_FR: "Pressing Jan Di" })];
  const [conflict] = findSpellingConflicts(entries, COL);
  assert.deepEqual(planUnify(entries, conflict, "Jan-Di"),
    [{ entryId: "잔디 세탁", column: "fr_FR", before: "Pressing Jan Di", after: "Pressing Jan-Di" }]);
});

test("conflictKey is stable but changes when a new spelling appears", () => {
  const base = [e("a", { fr_FR: "Geum Jan-Di" }), e("b", { fr_FR: "Pressing Jan Di" })];
  const [c] = findSpellingConflicts(base, COL);
  const same = findSpellingConflicts([...base].reverse(), COL)[0];
  assert.equal(conflictKey(c), conflictKey(same)); // 항목 순서와 무관
  const [more] = findSpellingConflicts([...base, e("c", { fr_FR: "Jan-di" })], COL);
  assert.notEqual(conflictKey(c), conflictKey(more)); // 새 표기가 생기면 다시 묻는다
});
