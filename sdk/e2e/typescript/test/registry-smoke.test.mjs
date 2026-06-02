import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCatalogIndex,
  CatalogIndex,
  countTokens,
  DecomposedCatalog,
  retrieveTools,
} from "cyt-indexer-sdk";

test("countTokens from npm package", () => {
  assert.ok(countTokens("hello") > 0);
});

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
