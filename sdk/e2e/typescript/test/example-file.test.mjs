import assert from "node:assert/strict";
import test from "node:test";

import {
  catalogDictFromSnapshot,
  extractSnapshotParts,
  loadSnapshot,
  parseTestArgs,
  resolveSnapshotPath,
  writeOutput,
} from "./example-snapshot.mjs";

const { file: exampleFile, output: outputFile } = parseTestArgs();

test(
  "decompose from example file",
  {
    skip: exampleFile
      ? false
      : "pass --file to run against a local debug snapshot",
  },
  () => {
    if (!exampleFile) {
      return;
    }
    const snapshotPath = resolveSnapshotPath(exampleFile);
    const data = loadSnapshot(snapshotPath);
    extractSnapshotParts(data);

    const catalog = catalogDictFromSnapshot(data);
    const jsonChunks = catalog.json ?? [];
    const mdChunks = catalog.md ?? [];

    assert.ok(
      jsonChunks.length > 0,
      "buildCatalogIndex produced no json chunks",
    );
    assert.ok(
      mdChunks.length > 0,
      "buildCatalogIndex produced no md enum chunks",
    );
    assert.ok(
      jsonChunks.some(
        (/** @type {{ file_path?: string }} */ entry) =>
          typeof entry.file_path === "string" &&
          entry.file_path.includes("/schemas/decomposed/") &&
          entry.file_path.endsWith(".json"),
      ),
      "expected per-property decomposed json chunks",
    );

    writeOutput(catalog, outputFile);
  },
);
