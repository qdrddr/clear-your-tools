import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  batchToolPassThrough,
  buildSkillNodeCatalog,
  classifyAndCountCatalog,
  PolicyContext,
} from "cyt-indexer-sdk";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "../../../..");

/** @param {string} name */
function loadFixture(name) {
  const path = join(repoRoot, "sdk", "e2e", "fixtures", name);
  return JSON.parse(readFileSync(path, "utf8"));
}

test("batchToolPassThrough from npm package", () => {
  const ctx = new PolicyContext("always_include", "always_include");
  const flags = batchToolPassThrough(["Agent", "grep"], ctx);
  assert.ok(flags.every((flag) => flag === true));
});

test("classifyAndCountCatalog from fixture", () => {
  const catalog = loadFixture("bm25_catalog.json");
  const result = classifyAndCountCatalog(catalog);
  assert.ok(typeof result.optional_chunk_count === "number");
});

test("buildSkillNodeCatalog empty entries smoke", () => {
  const nodes = buildSkillNodeCatalog([]);
  assert.deepEqual(nodes, []);
});
