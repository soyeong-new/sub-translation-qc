import test from "node:test";
import assert from "node:assert/strict";
import { matchFirstCase, planNameVariantChange } from "./nameVariants.js";

const group = {
  lines: [
    { segment_id: "a", surfaces: ["Jan Di"] },
    { segment_id: "b", surfaces: ["Jan-Di"] },
    { segment_id: "c", surfaces: ["Jan-di", "Jan Di"] },
  ],
};

test("lines already using the chosen spelling are not changed", () => {
  const rows = planNameVariantChange(group, "Jan-Di");
  assert.deepEqual(rows.map((r) => r.line.segment_id), ["a", "c"]);
  assert.deepEqual(rows[1].replacements, [
    { from_text: "Jan-di", to_text: "Jan-Di" }, { from_text: "Jan Di", to_text: "Jan-Di" }]);
});

test("choosing a non-recommended spelling changes the other lines", () => {
  assert.deepEqual(planNameVariantChange(group, "Jan Di").map((r) => r.line.segment_id), ["b", "c"]);
});

test("first-letter case follows the original place", () => {
  assert.equal(matchFirstCase("Sunbaenim", "sunbae nim"), "Sunbae nim");
  assert.equal(matchFirstCase("sunbaenim", "Sunbae nim"), "Sunbae nim");
  assert.equal(matchFirstCase("Shin Hwa", "Shinhwa"), "Shinhwa");
});

test("first-letter-only differences count as the same spelling", () => {
  const g = { lines: [{ segment_id: "a", surfaces: ["jan-Di"] }] };
  assert.deepEqual(planNameVariantChange(g, "Jan-Di"), []);
});
