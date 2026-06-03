import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCatalogFromTools,
  buildCatalogIndex,
  CatalogIndex,
  catalogToolCount,
} from "../build.js";

test("catalogToolCount counts tools in catalog dict", () => {
  const dict = {
    md: [],
    json: [{ id: "a" }, { id: "b" }],
    tools: [{ name: "a" }, { name: "b" }],
  };
  assert.equal(catalogToolCount(dict), 2);
});

test("CatalogIndex.toCatalogDict builds json and markdown entries", () => {
  const index = new CatalogIndex([], {
    "schemas/decomposed/search.json": '{"id":"search"}',
    "schemas/decomposed/search.md": "# search",
  });
  const dict = index.toCatalogDict("src/catalog");
  assert.equal(dict.json.length, 1);
  assert.equal(dict.md.length, 1);
  assert.equal(dict.json[0]?.id, "search");
});

test("CatalogIndex.toCatalogDict skips non-object JSON", () => {
  const dict = new CatalogIndex([], {
    "schemas/decomposed/broken.json": "[]",
  }).toCatalogDict();
  assert.equal(dict.json.length, 0);
});

test("buildCatalogIndex returns in-memory catalog", () => {
  const tools = [{ name: "search", description: "Search tool" }];
  const index = buildCatalogIndex(tools, ["fast", "slow"]);
  assert.ok(index instanceof CatalogIndex);
  assert.ok(index.tools.length >= 1);
  assert.ok(Object.keys(index.files).length >= 1);
});

test("buildCatalogFromTools accepts Anthropic API tool shape", () => {
  const index = buildCatalogFromTools([
    {
      name: "Agent",
      description: "Launch agents",
      input_schema: {
        type: "object",
        properties: { prompt: { type: "string" } },
        required: ["prompt"],
      },
    },
  ]);
  assert.ok(Object.keys(index.files).some((k) => k.includes("Agent.json")));
});
