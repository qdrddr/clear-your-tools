import assert from "node:assert/strict";
import test from "node:test";

import { CatalogIndex } from "../build.js";
import { DecomposedCatalog } from "../decomposed-catalog.js";
import {
  configureRuntimeDefaults,
  decomposedScore,
  enumScore,
} from "../runtime-defaults.js";
import { chunkSurvivorKey, removedChunks, retrieveTools } from "../retrieve.js";
import { decomposedPrefix } from "../paths.js";

test("score constants match Python SDK defaults", () => {
  configureRuntimeDefaults({
    decomposedScore: 0.5,
    enumScore: 0.2,
    rerankScore: 0.003,
    emptyOptionalFallbackK: 3,
    defaultSystemPolicy: "prune_optional",
    defaultMcpPolicy: "prune_all",
  });
  assert.equal(decomposedScore(), 0.5);
  assert.equal(enumScore(), 0.2);
});

test("DecomposedCatalog.fromCatalogIndex loads decomposed JSON files", () => {
  const index = new CatalogIndex([], {
    "schemas/decomposed/search.json": '{"id":"search"}',
    "schemas/decomposed/search.md": "# search",
    "other/path.json": '{"id":"ignored"}',
    "schemas/decomposed/array.json": "[]",
    "schemas/decomposed/valid.json": '{"id":"valid"}',
  });
  const store = DecomposedCatalog.fromCatalogIndex({
    tools: index.tools,
    files: index.files,
  });
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
  const toolJson = `${decomposedPrefix()}search.json`;
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

test("removedChunks excludes survivors by decomposed key", () => {
  const prefix = decomposedPrefix();
  const full = {
    json: [
      {
        file_path: `${prefix}Agent.json`,
        content: { name: "Agent" },
      },
      {
        file_path: `${prefix}Agent/extra.json`,
        content: {},
      },
    ],
    md: [
      { file_path: `${prefix}haiku.md`, content: "haiku" },
      { file_path: `${prefix}sonnet.md`, content: "sonnet" },
    ],
  };
  const surviving = {
    json: [{ file_path: `src/catalog/${prefix}Agent.json` }],
    md: [{ file_path: `src/catalog/${prefix}haiku.md` }],
  };
  const removed = removedChunks(full, surviving);
  assert.equal(removed.json.length, 1);
  assert.equal(removed.json[0]?.file_path, `${prefix}Agent/extra.json`);
  assert.equal(removed.md.length, 1);
  assert.equal(removed.md[0]?.file_path, `${prefix}sonnet.md`);
});

test("chunkSurvivorKey normalizes file paths", () => {
  const prefix = decomposedPrefix();
  assert.equal(
    chunkSurvivorKey({ file_path: `src/catalog/${prefix}Agent.json` }, "json"),
    `${prefix}Agent.json`,
  );
});

test("retrieveTools treats non-object data as empty catalog dict", () => {
  const toolJson = `${decomposedPrefix()}search.json`;
  const catalog = new DecomposedCatalog({
    [toolJson]: { type: "object", properties: {} },
  });
  const result = retrieveTools(null, { catalog });
  assert.ok(Array.isArray(result));
});
