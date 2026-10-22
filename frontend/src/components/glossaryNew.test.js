import test from "node:test";
import assert from "node:assert/strict";
import { collectNewIds } from "./glossaryNew.js";

test("collectNewIds gathers every new spelling id across entries and columns", () => {
  const entries = [
    { id: "a", new: { es_LATAM: "s1", fr_FR: "s2" } },
    { id: "b", new: {} },
    { id: "c" },
    { id: "d", new: { es_LATAM: "s3" } },
  ];
  assert.deepEqual(collectNewIds(entries).sort(), ["s1", "s2", "s3"]);
});

test("collectNewIds returns empty when nothing is new", () => {
  assert.deepEqual(collectNewIds([{ id: "a", new: {} }]), []);
  assert.deepEqual(collectNewIds([]), []);
});
