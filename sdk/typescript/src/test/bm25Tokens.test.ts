import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { bm25CohesionChunk, bm25ScoreCatalog, countTokens } from "../index.js";

const fixturesDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../e2e/fixtures",
);

test("countTokens smoke", () => {
  assert.ok(countTokens("hello world") >= 1);
});

test("bm25ScoreCatalog smoke", () => {
  const catalog = JSON.parse(
    readFileSync(join(fixturesDir, "bm25_catalog.json"), "utf8"),
  );
  const scored = bm25ScoreCatalog(catalog, "read files disk") as {
    json: Array<{ score: string }>;
  };
  const scores = scored.json.map((item) => Number.parseFloat(item.score));
  assert.ok(scores[0] > scores[1]);
});

test("bm25CohesionChunk smoke", () => {
  const sample = readFileSync(join(fixturesDir, "cohesion_sample.md"), "utf8");
  const cfg = JSON.parse(
    readFileSync(join(fixturesDir, "cohesion_config.json"), "utf8"),
  );
  const chunks = bm25CohesionChunk(sample, {
    windowMode: cfg.window_mode,
    chunkSize: cfg.chunk_size,
    similarityWindow: cfg.similarity_window,
    skipWindow: cfg.skip_window,
    tokenCounter: cfg.token_counter,
  });
  assert.ok(chunks.length > 0);
  const recompiled = chunks.map((chunk) => chunk.text).join("");
  assert.equal(recompiled, sample);
});
