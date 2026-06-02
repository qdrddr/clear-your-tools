import assert from "node:assert/strict";
import test from "node:test";

import { CatalogIndex } from "../build.js";
import { DecomposedCatalog } from "../decomposed-catalog.js";
import { DECOMPOSED_SCORE, ENUM_SCORE, retrieveTools } from "../retrieve.js";
import { DECOMPOSED_PREFIX } from "../paths.js";

test("score constants match Python SDK", () => {
  assert.equal(DECOMPOSED_SCORE, 0.5);
  assert.equal(ENUM_SCORE, 0.2);
});

test("DecomposedCatalog.fromCatalogIndex loads decomposed JSON files", () => {
  const index = new CatalogIndex([], {
    "schemas/decomposed/search.json": '{"id":"search"}',
    "schemas/decomposed/search.md": "# search",
    "other/path.json": '{"id":"ignored"}',
    "schemas/decomposed/array.json": "[]",
    "schemas/decomposed/valid.json": '{"id":"valid"}',
  });
  const store = DecomposedCatalog.fromCatalogIndex(index);
  assert.equal(store.hasJson("schemas/decomposed/search.json"), true);
  assert.equal(store.getJson("schemas/decomposed/search.json")?.id, "search");
  assert.equal(store.hasJson("schemas/decomposed/search.md"), false);
  assert.equal(store.hasJson("other/path.json"), false);
  assert.equal(store.hasJson("schemas/decomposed/array.json"), false);
  assert.equal(store.getJson("schemas/decomposed/valid.json")?.id, "valid");
});

test("DecomposedCatalog.fromCatalogDict parses survivor entries", () => {
  const store = DecomposedCatalog.fromCatalogDict({
    json: [
      {
        file_path: "src/catalog/schemas/decomposed/search/query.json",
        content: { id: "query" },
      },
      {
        file_path: "src/catalog/schemas/decomposed/broken.json",
        content: null,
      },
    ],
  });
  assert.equal(
    store.resolveKey("src/catalog/schemas/decomposed/search/query.json"),
    "schemas/decomposed/search/query.json",
  );
  assert.equal(
    store.getJson("schemas/decomposed/search/query.json")?.id,
    "query",
  );
});

test("DecomposedCatalog resolveKey and toJsonFiles", () => {
  const store = new DecomposedCatalog({
    "schemas/decomposed/search.json": { id: "search" },
  });
  assert.equal(
    store.resolveKey("schemas/decomposed/search.json"),
    "schemas/decomposed/search.json",
  );
  assert.equal(
    store.resolveKey("src/catalog/schemas/decomposed/search.json"),
    "schemas/decomposed/search.json",
  );
  assert.equal(store.resolveKey("missing.json"), null);
  assert.deepEqual(store.toJsonFiles(), {
    "schemas/decomposed/search.json": { id: "search" },
  });
});

test("retrieveTools accepts DecomposedCatalog and CatalogIndex", () => {
  const toolJson = `${DECOMPOSED_PREFIX}search.json`;
  const catalog = new DecomposedCatalog({
    [toolJson]: {
      type: "object",
      properties: { query: { type: "string" } },
    },
  });
  const survivorData = {
    json: [
      {
        file_path: `src/catalog/${toolJson}`,
        content: { type: "object", properties: { query: { type: "string" } } },
      },
    ],
  };

  const fromDecomposed = retrieveTools(survivorData, { catalog });
  assert.ok(Array.isArray(fromDecomposed));

  const fromIndex = retrieveTools(survivorData, {
    catalog: new CatalogIndex([], {
      [toolJson]: JSON.stringify(catalog.getJson(toolJson)),
    }),
  });
  assert.ok(Array.isArray(fromIndex));
});

test("retrieveTools rejects invalid catalog type", () => {
  assert.throws(
    () => retrieveTools({}, { catalog: {} as CatalogIndex }),
    TypeError,
  );
});

test("retrieveTools treats non-object data as empty catalog dict", () => {
  const toolJson = `${DECOMPOSED_PREFIX}search.json`;
  const catalog = new DecomposedCatalog({
    [toolJson]: { type: "object", properties: {} },
  });
  const result = retrieveTools(null, { catalog });
  assert.ok(Array.isArray(result));
});
