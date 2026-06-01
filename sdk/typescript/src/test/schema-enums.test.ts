import assert from "node:assert/strict";
import test from "node:test";

import { collectSchemaEnums } from "../catalog-json.js";

test("collectSchemaEnums walks nested schema objects", () => {
  const schema = {
    type: "object",
    properties: {
      mode: { enum: ["fast", "slow"] },
      nested: [{ enum: ["a", "b"] }],
    },
  };
  assert.deepEqual([...collectSchemaEnums(schema)].sort(), [
    "a",
    "b",
    "fast",
    "slow",
  ]);
});

test("collectSchemaEnums ignores primitives and empty input", () => {
  assert.deepEqual(collectSchemaEnums(null), []);
  assert.deepEqual(collectSchemaEnums("text"), []);
  assert.deepEqual(collectSchemaEnums(42), []);
});

test("collectSchemaEnums collects enums from arrays at the root", () => {
  assert.deepEqual(
    [...collectSchemaEnums([{ enum: ["x"] }, { enum: ["y"] }])].sort(),
    ["x", "y"],
  );
});
