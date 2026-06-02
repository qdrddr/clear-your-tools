#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const here = dirname(fileURLToPath(import.meta.url));

const { values, positionals } = parseArgs({
  options: {
    file: { type: "string" },
    output: { type: "string" },
  },
  allowPositionals: true,
});

if (values.file) {
  process.env.CYT_E2E_FILE = values.file;
}
if (values.output) {
  process.env.CYT_E2E_OUTPUT = values.output;
}

const testFiles = readdirSync(here)
  .filter((name) => name.endsWith(".test.mjs"))
  .map((name) => join(here, name));

const nodeArgs = ["--test", ...testFiles, ...positionals];
const result = spawnSync(process.execPath, nodeArgs, {
  stdio: "inherit",
  env: process.env,
});

process.exit(result.status ?? 1);
