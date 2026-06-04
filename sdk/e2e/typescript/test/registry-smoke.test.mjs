import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCatalogIndex,
  CatalogIndex,
  DecomposedCatalog,
  removedChunks,
  retrieveTools,
} from "cyt-indexer-sdk";

test("buildCatalogIndex from npm package", () => {
  const tool = {
    id: "mcp__test__foo",
    server: "test",
    tool: "mcp__test__foo",
    summary: "A test tool",
    full_schema: {
      id: "mcp__test__foo",
      name: "mcp__test__foo",
      description: "A test tool",
      inputSchema: {
        type: "object",
        properties: {
          required_field: { type: "string" },
          optional_field: { type: "string", description: "opt" },
        },
        required: ["required_field"],
      },
    },
  };
  const index = buildCatalogIndex([tool], []);
  assert.ok(
    Object.hasOwn(index.files, "schemas/decomposed/mcp__test__foo.json"),
  );
});

test("removedChunks from npm package", () => {
  const prefix = "schemas/decomposed/";
  const full = {
    json: [
      { file_path: `${prefix}Agent.json`, content: { name: "Agent" } },
      { file_path: `${prefix}Agent/extra.json`, content: {} },
    ],
    md: [],
  };
  const surviving = {
    json: [{ file_path: `src/catalog/${prefix}Agent.json` }],
    md: [],
  };
  const removed = removedChunks(full, surviving);
  assert.equal(removed.json.length, 1);
  assert.equal(removed.json[0].file_path, `${prefix}Agent/extra.json`);
});

test("retrieveTools from npm package", () => {
  const toolJson = "schemas/decomposed/search.json";
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
        content: {
          type: "object",
          properties: { query: { type: "string" } },
        },
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
