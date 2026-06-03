import assert from "node:assert/strict";
import test from "node:test";

import { collectEnums } from "../build.js";
import {
  decomposedPrefix,
  getRootToolKey,
  jsonExt,
  mdExt,
  toDecomposedKey,
  toolIdFromDecomposedRel,
} from "../paths.js";

test("path constants are stable", () => {
  assert.equal(jsonExt(), ".json");
  assert.equal(mdExt(), ".md");
  assert.equal(decomposedPrefix(), "schemas/decomposed/");
});

test("toDecomposedKey normalizes catalog paths", () => {
  assert.equal(
    toDecomposedKey("src/catalog/schemas/decomposed/foo.json"),
    "schemas/decomposed/foo.json",
  );
  assert.equal(toDecomposedKey("other/path.json"), null);
});

test("toolIdFromDecomposedRel extracts tool ids", () => {
  assert.equal(
    toolIdFromDecomposedRel(`${decomposedPrefix()}search/query.json`),
    "search",
  );
  assert.equal(toolIdFromDecomposedRel("search/query.json"), "search");
});

test("getRootToolKey resolves nested paths to root tool json", () => {
  assert.equal(
    getRootToolKey(`${decomposedPrefix()}search/query/fields/name.json`),
    `${decomposedPrefix()}search.json`,
  );
  assert.equal(
    getRootToolKey(`${decomposedPrefix()}search.json`),
    `${decomposedPrefix()}search.json`,
  );
  assert.equal(getRootToolKey("not/a/decomposed/path.json"), null);
  assert.equal(getRootToolKey(`${decomposedPrefix()}`), null);
});

test("collectEnums walks nested schema objects", () => {
  const schema = {
    type: "object",
    properties: {
      mode: { enum: ["fast", "slow"] },
      nested: [{ enum: ["a", "b"] }],
    },
  };
  assert.deepEqual([...collectEnums(schema)].sort(), [
    "a",
    "b",
    "fast",
    "slow",
  ]);
});

test("collectEnums ignores primitives and empty input", () => {
  assert.deepEqual(collectEnums(null), []);
  assert.deepEqual(collectEnums("text"), []);
  assert.deepEqual(collectEnums(42), []);
});

test("collectEnums collects enums from arrays at the root", () => {
  assert.deepEqual(
    [...collectEnums([{ enum: ["x"] }, { enum: ["y"] }])].sort(),
    ["x", "y"],
  );
});
