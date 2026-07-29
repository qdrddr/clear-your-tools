import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  buildSkillNodeCatalog,
  classifyAndCountCatalog,
} from "../../pipeline.js";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../e2e/fixtures/bm25_catalog.json",
);

test("classifyAndCountCatalog reports optional chunk counts from fixture", () => {
  const catalog = JSON.parse(readFileSync(fixturePath, "utf8")) as Record<
    string,
    unknown
  >;
  const result = classifyAndCountCatalog(catalog);
  assert.ok(typeof result.optional_chunk_count === "number");
  assert.ok(Array.isArray(result.system_optional));
  assert.ok(Array.isArray(result.mcp_optional));
});

test("buildSkillNodeCatalog returns empty array for no entries", () => {
  const nodes = buildSkillNodeCatalog([]);
  assert.deepEqual(nodes, []);
});
