import assert from "node:assert/strict";
import test from "node:test";

import { collectEnums } from "../build.js";
import {
  DECOMPOSED_PREFIX,
  getRootToolKey,
  JSON_EXT,
  MD_EXT,
  toDecomposedKey,
  toolIdFromDecomposedRel,
} from "../paths.js";

test("path constants are stable", () => {
  assert.equal(JSON_EXT, ".json");
  assert.equal(MD_EXT, ".md");
  assert.equal(DECOMPOSED_PREFIX, "schemas/decomposed/");
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
    toolIdFromDecomposedRel(`${DECOMPOSED_PREFIX}search/query.json`),
    "search",
  );
  assert.equal(toolIdFromDecomposedRel("search/query.json"), "search");
});

test("getRootToolKey resolves nested paths to root tool json", () => {
  assert.equal(
    getRootToolKey(`${DECOMPOSED_PREFIX}search/query/fields/name.json`),
    `${DECOMPOSED_PREFIX}search.json`,
  );
  assert.equal(
    getRootToolKey(`${DECOMPOSED_PREFIX}search.json`),
    `${DECOMPOSED_PREFIX}search.json`,
  );
  assert.equal(getRootToolKey("not/a/decomposed/path.json"), null);
  assert.equal(getRootToolKey(`${DECOMPOSED_PREFIX}`), null);
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
