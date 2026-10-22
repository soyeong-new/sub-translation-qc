import test from "node:test";
import assert from "node:assert/strict";
import { orderGlossaryEntries } from "./glossaryOrder.js";

const e = (term, category, spellings) => ({ id: term, korean_term: term, category, spellings });

test("blank amount entries go to pending, everything else sorted in rest", () => {
  const { pending, rest } = orderGlossaryEntries([
    e("세나", "person", { es_LATAM: "Sena" }),
    e("100억", "amount", {}),
    e("10억", "amount", { es_LATAM: "mil millones de wones" }),
    e("가게", "place", {}),
  ], ["es_LATAM"]);
  assert.deepEqual(pending.map((x) => x.korean_term), ["100억"]);
  assert.deepEqual(rest.map((x) => x.korean_term), ["10억", "가게", "세나"]);
});

test("amount entry filled in every column leaves pending", () => {
  const { pending } = orderGlossaryEntries([e("100억", "amount", { es_LATAM: "x" })], ["es_LATAM"]);
  assert.equal(pending.length, 0);
});

test("amount entry blank in any one column stays pending", () => {
  const { pending } = orderGlossaryEntries(
    [e("100억", "amount", { es_LATAM: "x" })], ["es_LATAM", "fr_FR"]);
  assert.equal(pending.length, 1);
});
