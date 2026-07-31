import {
  accessSync,
  constants,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { buildCatalogIndex } from "cyt-indexer-sdk";

/** @typedef {Record<string, unknown>} JsonRecord */
/** @typedef {{ content?: unknown; file_path?: string; id?: string }} CatalogEntry */
/** @typedef {{ json?: CatalogEntry[]; md?: CatalogEntry[]; tools?: JsonRecord[] }} SnapshotStage */
/** @typedef {{ pruning?: { decomposed_catalog?: Record<string, SnapshotStage> }; body?: { tools?: JsonRecord[] }; tools?: JsonRecord[] }} SnapshotData */

const REPO_ROOT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "..",
);

/** @param {string} path @param {string} root */
function isUnderRoot(path, root) {
  const rel = path.startsWith(`${root}${sep}`)
    ? path.slice(root.length + 1)
    : path === root
      ? ""
      : null;
  if (rel === null) {
    return false;
  }
  return rel === "" || (!rel.startsWith("..") && !rel.includes(`/..${sep}`));
}

/**
 * Resolve path under REPO_ROOT and rebuild via join(root, rel) so traversal
 * outside the repo is rejected before any file I/O.
 *
 * @param {string} path
 * @returns {string}
 */
function safePathUnderRoot(path) {
  const root = REPO_ROOT;
  const prefix = `${root}${sep}`;
  const candidates = [path, join(root, path)];

  for (const candidate of candidates) {
    const abs = resolve(candidate);
    if (abs !== root && !abs.startsWith(prefix)) {
      continue;
    }

    const rel =
      abs === root
        ? ""
        : abs.startsWith(prefix)
          ? abs.slice(prefix.length)
          : null;
    if (rel === null || rel.includes("..") || rel.includes(`/..${sep}`)) {
      continue;
    }

    return rel === "" ? root : join(root, rel);
  }

  throw new Error(`path must stay under repo root ${REPO_ROOT}: ${path}`);
}

/** @param {string} path */
function resolveUnderRepo(path) {
  const safe = safePathUnderRoot(path);
  try {
    accessSync(safe, constants.R_OK);
    return safe;
  } catch {
    throw new Error(
      `snapshot file not found under repo root ${REPO_ROOT}: ${path}`,
    );
  }
}

/** @param {string} path */
function resolveOutputUnderRepo(path) {
  return safePathUnderRoot(path);
}

/** @returns {{ file: string | null; output: string | null }} */
function readEnvArgs() {
  const file = process.env.CYT_E2E_FILE ?? process.env.npm_config_file ?? null;
  const output =
    process.env.CYT_E2E_OUTPUT ?? process.env.npm_config_output ?? null;
  return {
    file: file ? resolveUnderRepo(file) : null,
    output: output ? resolveOutputUnderRepo(output) : null,
  };
}

/**
 * @param {string[]} argv
 * @param {number} index
 * @param {string} flag
 * @returns {{ value: string | null; nextIndex: number } | null}
 */
function readArgvFlag(argv, index, flag) {
  const arg = argv[index];
  const prefix = `${flag}=`;
  if (arg === flag) {
    return { value: argv[index + 1] ?? null, nextIndex: index + 1 };
  }
  if (arg.startsWith(prefix)) {
    return { value: arg.slice(prefix.length), nextIndex: index };
  }
  return null;
}

/**
 * @param {string[] | undefined} [argv]
 * @returns {{ file: string | null; output: string | null }}
 */
export function parseTestArgs(argv = process.argv) {
  const envArgs = readEnvArgs();
  if (envArgs.file || envArgs.output) {
    return envArgs;
  }

  /** @type {string | null} */
  let file = null;
  /** @type {string | null} */
  let output = null;

  for (let i = 0; i < argv.length; i += 1) {
    const fileFlag = readArgvFlag(argv, i, "--file");
    if (fileFlag) {
      file = fileFlag.value;
      i = fileFlag.nextIndex;
      continue;
    }
    const outputFlag = readArgvFlag(argv, i, "--output");
    if (outputFlag) {
      output = outputFlag.value;
      i = outputFlag.nextIndex;
    }
  }

  return {
    file: file ? resolveUnderRepo(file) : null,
    output: output ? resolveOutputUnderRepo(output) : null,
  };
}

/**
 * @param {string} path
 * @returns {string}
 */
export function resolveSnapshotPath(path) {
  return resolveUnderRepo(path);
}

/**
 * @param {string} resolvedPath
 * @returns {SnapshotData}
 */
export function loadSnapshotAt(resolvedPath) {
  const safe = safePathUnderRoot(resolvedPath);
  if (!isUnderRoot(safe, REPO_ROOT)) {
    throw new Error(
      `snapshot path must stay under repo root ${REPO_ROOT}, got ${safe}`,
    );
  }
  const raw = readFileSync(safe, "utf8");
  const data = JSON.parse(raw);
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new TypeError(`expected JSON object in ${safe}`);
  }
  return data;
}

/**
 * @param {string} path
 * @returns {SnapshotData}
 */
export function loadSnapshot(path) {
  return loadSnapshotAt(resolveUnderRepo(path));
}

/**
 * @param {CatalogEntry[]} mdEntries
 * @returns {string[]}
 */
function enumsFromMd(mdEntries) {
  return mdEntries
    .filter(
      (/** @type {CatalogEntry} */ entry) =>
        entry && typeof entry.content === "string",
    )
    .map((/** @type {CatalogEntry} */ entry) => String(entry.content));
}

/**
 * @param {SnapshotStage} stage
 */
function survivorCatalog(stage) {
  /** @type {{ json?: unknown[]; md?: unknown[] }} */
  const survivor = {};
  if (Array.isArray(stage.json)) {
    survivor.json = stage.json;
  }
  if (Array.isArray(stage.md)) {
    survivor.md = stage.md;
  }
  return survivor;
}

/**
 * @param {Record<string, SnapshotStage>} stages
 * @param {SnapshotStage} buildStage
 */
function survivorStageForSimpleSnapshot(stages, buildStage) {
  if (Array.isArray(stages.json) || Array.isArray(stages.md)) {
    return stages;
  }
  return buildStage;
}

/**
 * @param {SnapshotData} data
 */
function snapshotStages(data) {
  const stages = data.pruning?.decomposed_catalog ?? {};
  const buildStage = stages.build_index ?? {};

  if ("body" in data) {
    return {
      expected: data.body?.tools ?? [],
      buildStage,
      survivorStage: stages.rerank ?? buildStage,
    };
  }

  return {
    expected: data.tools ?? [],
    buildStage,
    survivorStage: survivorStageForSimpleSnapshot(stages, buildStage),
  };
}

/**
 * @param {JsonRecord[]} buildTools
 * @param {JsonRecord[]} expected
 */
function requireBuildTools(buildTools, expected) {
  if (buildTools.length === 0 && expected.length > 0) {
    throw new Error(
      "snapshot has no pruning.decomposed_catalog.build_index.tools; cannot rebuild catalog index",
    );
  }
}

/**
 * @param {{ json?: unknown[]; md?: unknown[] }} survivor
 */
function requireSurvivorCatalog(survivor) {
  const hasJson = Array.isArray(survivor.json) && survivor.json.length > 0;
  const hasMd = Array.isArray(survivor.md) && survivor.md.length > 0;
  if (!hasJson && !hasMd) {
    throw new Error("snapshot has no rerank json/md entries for decomposition");
  }
}

/**
 * @param {SnapshotData} data
 */
export function extractSnapshotParts(data) {
  const { expected, buildStage, survivorStage } = snapshotStages(data);
  const buildTools = buildStage.tools ?? [];
  requireBuildTools(buildTools, expected);

  const survivor = survivorCatalog(survivorStage);
  requireSurvivorCatalog(survivor);

  return { buildTools, survivor, expected };
}

/**
 * @param {SnapshotData} data
 */
export function catalogDictFromSnapshot(data) {
  const { buildTools } = extractSnapshotParts(data);
  const buildStage = data.pruning?.decomposed_catalog?.build_index ?? {};
  const enums = enumsFromMd(buildStage.md ?? []);
  const index = buildCatalogIndex(buildTools, enums);
  return index.toCatalogDict();
}

/**
 * @param {{ md: JsonRecord[]; json: JsonRecord[]; tools: JsonRecord[] }} catalog
 * @param {string} resolvedOutputPath
 */
export function writeOutputAt(catalog, resolvedOutputPath) {
  const safe = safePathUnderRoot(resolvedOutputPath);
  if (!isUnderRoot(safe, REPO_ROOT)) {
    throw new Error(
      `output path must stay under repo root ${REPO_ROOT}, got ${safe}`,
    );
  }
  const payload = `${JSON.stringify(catalog, null, 2)}\n`;
  mkdirSync(dirname(safe), { recursive: true });
  writeFileSync(safe, payload, "utf8");
}

/**
 * @param {{ md: JsonRecord[]; json: JsonRecord[]; tools: JsonRecord[] }} catalog
 * @param {string | null | undefined} outputPath
 */
export function writeOutput(catalog, outputPath) {
  if (outputPath) {
    writeOutputAt(catalog, resolveOutputUnderRepo(outputPath));
    return;
  }
  process.stdout.write(`${JSON.stringify(catalog, null, 2)}\n`);
}

/** Run the example-file e2e decomposition flow (parse args, load, assert, write). */
export function runExampleFileTest() {
  const { file: snapshotPath, output: outputFile } = parseTestArgs();
  if (!snapshotPath) {
    return;
  }

  const data = loadSnapshotAt(snapshotPath);
  extractSnapshotParts(data);

  const catalog = catalogDictFromSnapshot(data);
  const jsonChunks = catalog.json ?? [];
  const mdChunks = catalog.md ?? [];

  if (jsonChunks.length === 0) {
    throw new Error("buildCatalogIndex produced no json chunks");
  }
  if (mdChunks.length === 0) {
    throw new Error("buildCatalogIndex produced no md enum chunks");
  }
  const hasDecomposed = jsonChunks.some(
    (/** @type {{ file_path?: string }} */ entry) =>
      typeof entry.file_path === "string" &&
      entry.file_path.includes("/schemas/decomposed/") &&
      entry.file_path.endsWith(".json"),
  );
  if (!hasDecomposed) {
    throw new Error("expected per-property decomposed json chunks");
  }

  if (outputFile) {
    writeOutputAt(catalog, outputFile);
  } else {
    process.stdout.write(`${JSON.stringify(catalog, null, 2)}\n`);
  }
}
