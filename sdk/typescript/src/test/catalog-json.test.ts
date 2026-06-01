import assert from "node:assert/strict";
import test from "node:test";

import {
  maybeDecomposedJsonFile,
  parseCatalogJsonEntry,
  parseDecomposedJsonContent,
} from "../catalog-json.js";
import { DECOMPOSED_PREFIX } from "../paths.js";
import { isJsonRecord } from "../types.js";

test("isJsonRecord accepts plain objects only", () => {
  assert.equal(isJsonRecord({ ok: true }), true);
  assert.equal(isJsonRecord(null), false);
  assert.equal(isJsonRecord([]), false);
  assert.equal(isJsonRecord("x"), false);
});

test("parseDecomposedJsonContent validates object JSON", () => {
  assert.deepEqual(parseDecomposedJsonContent('{"id":"x"}'), { id: "x" });
  assert.equal(parseDecomposedJsonContent("[]"), null);
  assert.equal(parseDecomposedJsonContent('"text"'), null);
});

test("maybeDecomposedJsonFile filters by decomposed path", () => {
  assert.deepEqual(
    maybeDecomposedJsonFile(
      `${DECOMPOSED_PREFIX}search.json`,
      '{"id":"search"}',
    ),
    { id: "search" },
  );
  assert.equal(
    maybeDecomposedJsonFile("other/search.json", '{"id":"search"}'),
    null,
  );
});

test("parseCatalogJsonEntry normalizes survivor entries", () => {
  assert.deepEqual(
    parseCatalogJsonEntry({
      file_path: `src/catalog/${DECOMPOSED_PREFIX}search/query.json`,
      content: { id: "query" },
    }),
    ["schemas/decomposed/search/query.json", { id: "query" }],
  );
  assert.equal(
    parseCatalogJsonEntry({ file_path: "bad", content: null }),
    null,
  );
});
