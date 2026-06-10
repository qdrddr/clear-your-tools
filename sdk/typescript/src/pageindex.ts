/** Skills pageindex (markdown tree indexing and retrieval). */

import {
  SkillsBuilderNative,
  buildSkillsIndexNative,
  getSkillContentRetrieveResultNative,
  getSkillDocumentNative,
  getSkillLineContentFromSpecNative,
  getSkillLineContentNative,
  getSkillStructureNative,
  loadSkillsIndexFromDirNative,
  mdToTreeNative,
  parseSkillChunkIdsNative,
  parseSkillNodeIdsNative,
  reconstructSkillMarkdownNative,
  skillsIndexFromDecomposedDirNative,
  writeReconstructedSkillNative,
  writeSkillsIndexNative,
  type ReconstructOptionsNapi,
} from "./native.js";
import {
  cohesionConfigToNative,
  defaultBm25CohesionConfig,
  type Bm25CohesionConfig,
} from "./bm25Cohesion.js";

export type { Bm25CohesionConfig } from "./bm25Cohesion.js";

export interface PageIndexConfig {
  ifAddNodeId?: boolean;
  ifAddNodeText?: boolean;
  /** When false, skills build skips BM25 cohesion chunking (node-level only). Default: true. */
  enableBm25Chunking?: boolean;
  bm25Cohesion?: Partial<Bm25CohesionConfig>;
}

/** CamelCase SDK config or snake_case partial dict (e.g. from app YAML). */
export type PageIndexConfigInput = PageIndexConfig | Record<string, unknown>;

export interface ReconstructOptions {
  keepAllHeaders?: boolean;
}

export interface SkillsIndexDict {
  documents: Record<string, unknown>;
  files: Record<string, string>;
}

const BM25_FLAT_KEYS = new Set([
  "window_mode",
  "threshold",
  "merge_threshold",
  "chunk_size",
  "token_counter",
  "similarity_window",
  "next_unit_size",
  "skip_window",
  "min_units_per_chunk",
  "minimum_words",
  "minimum_sentences",
  "min_characters_per_sentence",
  "min_characters_per_word",
  "delimiters",
  "include_delim",
  "use_stopwords",
  "filter_window",
  "filter_polyorder",
  "filter_tolerance",
  "stem_language",
]);

export function defaultPageIndexConfig(): PageIndexConfig {
  return {
    ifAddNodeId: true,
    ifAddNodeText: false,
    enableBm25Chunking: true,
    bm25Cohesion: defaultBm25CohesionConfig(),
  };
}

/** Pageindex config that returns node-level data only (no BM25 cohesion chunking). */
export function pageIndexConfigWithoutChunking(): PageIndexConfig {
  return {
    ...defaultPageIndexConfig(),
    enableBm25Chunking: false,
  };
}

/** Partial pageindex settings from app config; Rust merges unset keys with SDK defaults. */
export function pageIndexConfigFromMapping(
  mapping?: Record<string, unknown> | null,
): Record<string, unknown> | undefined {
  if (mapping == null) return undefined;
  if (isSnakeCasePageIndexDict(mapping)) return mapping;
  return pageIndexConfigToNative(pageIndexConfigFromPartial(mapping));
}

export function pageIndexConfigFromPartial(
  partial: Partial<PageIndexConfig> & Record<string, unknown>,
): PageIndexConfig {
  const cfg = defaultPageIndexConfig();
  if (partial.ifAddNodeId !== undefined) cfg.ifAddNodeId = partial.ifAddNodeId;
  if (partial.ifAddNodeText !== undefined)
    cfg.ifAddNodeText = partial.ifAddNodeText;
  if (partial.enableBm25Chunking !== undefined)
    cfg.enableBm25Chunking = partial.enableBm25Chunking;
  if (partial.bm25Cohesion !== undefined) {
    cfg.bm25Cohesion = {
      ...defaultBm25CohesionConfig(),
      ...partial.bm25Cohesion,
    };
  }
  return cfg;
}

export function pageIndexConfigToNative(
  config: PageIndexConfig,
): Record<string, unknown> {
  const out: Record<string, unknown> = {
    if_add_node_id: config.ifAddNodeId ?? true,
    if_add_node_text: config.ifAddNodeText ?? false,
    enable_bm25_chunking: config.enableBm25Chunking ?? true,
  };
  const cohesion = cohesionConfigToNative(
    config.bm25Cohesion ?? defaultBm25CohesionConfig(),
  );
  if (cohesion !== undefined) {
    out.bm25_cohesion = cohesion;
  }
  return out;
}

function isSnakeCasePageIndexDict(config: Record<string, unknown>): boolean {
  return (
    "if_add_node_id" in config ||
    "if_add_node_text" in config ||
    "enable_bm25_chunking" in config ||
    "bm25_cohesion" in config ||
    "chunk_size" in config ||
    [...BM25_FLAT_KEYS].some((key) => key in config)
  );
}

function resolveNativeConfig(
  config?: PageIndexConfigInput,
): Record<string, unknown> | undefined {
  if (config == null) return undefined;
  if (isSnakeCasePageIndexDict(config as Record<string, unknown>)) {
    return config as Record<string, unknown>;
  }
  return pageIndexConfigToNative(config as PageIndexConfig);
}

export function buildSkillsIndex(
  skillDirs: string[],
  config?: PageIndexConfigInput,
): SkillsIndexDict {
  return buildSkillsIndexNative(
    skillDirs,
    resolveNativeConfig(config),
  ) as SkillsIndexDict;
}

export function writeSkillsIndex(
  index: SkillsIndexDict,
  outputDir: string,
): void {
  writeSkillsIndexNative(index, outputDir);
}

export function loadSkillsIndexFromDir(catalogDir: string): SkillsIndexDict {
  return loadSkillsIndexFromDirNative(catalogDir) as SkillsIndexDict;
}

export function skillsIndexFromDecomposedDir(dir: string): SkillsIndexDict {
  return skillsIndexFromDecomposedDirNative(dir) as SkillsIndexDict;
}

export function mdToTree(
  markdownContent: string,
  sourcePath: string,
  config?: PageIndexConfigInput,
): Record<string, unknown> {
  return mdToTreeNative(
    markdownContent,
    sourcePath,
    resolveNativeConfig(config),
  ) as Record<string, unknown>;
}

export function getSkillDocument(
  documents: Record<string, unknown>,
  docId: string,
): Record<string, unknown> {
  return getSkillDocumentNative(documents, docId) as Record<string, unknown>;
}

export function getSkillStructure(
  documents: Record<string, unknown>,
  docId: string,
): unknown {
  return getSkillStructureNative(documents, docId);
}

export function getSkillLineContentFromSpec(
  index: SkillsIndexDict,
  docId: string,
  lineNumSpec: string,
): Array<{ line_num: number; node_id: number; content: string }> {
  return getSkillLineContentFromSpecNative(index, docId, lineNumSpec) as Array<{
    line_num: number;
    node_id: number;
    content: string;
  }>;
}

function toNativeReconstructOptions(
  options?: ReconstructOptions,
): ReconstructOptionsNapi | undefined {
  if (!options) return undefined;
  return { keepAllHeaders: options.keepAllHeaders ?? false };
}

export function getSkillLineContent(
  index: SkillsIndexDict,
  docId: string,
  opts?: {
    lineNumSpecs?: string[];
    nodeIdSpecs?: string[];
    chunkIdSpecs?: string[];
  },
): Array<{
  line_num: number;
  node_id: number;
  content: string;
  chunk_id?: number;
}> {
  return getSkillLineContentNative(
    index,
    docId,
    opts?.lineNumSpecs,
    opts?.nodeIdSpecs,
    opts?.chunkIdSpecs,
  ) as Array<{
    line_num: number;
    node_id: number;
    content: string;
    chunk_id?: number;
  }>;
}

export function getSkillContentRetrieveResult(
  index: SkillsIndexDict,
  docId: string,
  opts?: {
    lineNumSpecs?: string[];
    nodeIdSpecs?: string[];
    chunkIdSpecs?: string[];
    options?: ReconstructOptions;
  },
): Record<string, unknown> {
  return getSkillContentRetrieveResultNative(
    index,
    docId,
    opts?.lineNumSpecs,
    opts?.nodeIdSpecs,
    opts?.chunkIdSpecs,
    toNativeReconstructOptions(opts?.options),
  ) as Record<string, unknown>;
}

export function reconstructSkillMarkdown(
  index: SkillsIndexDict,
  docId: string,
  opts?: {
    lineNumSpecs?: string[];
    nodeIdSpecs?: string[];
    chunkIdSpecs?: string[];
    options?: ReconstructOptions;
  },
): {
  markdown: string;
  matched_node_ids: number[];
  matched_chunk_ids: number[];
  node_ids: number[];
  output_rel_path: string;
} {
  return reconstructSkillMarkdownNative(
    index,
    docId,
    opts?.lineNumSpecs,
    opts?.nodeIdSpecs,
    opts?.chunkIdSpecs,
    toNativeReconstructOptions(opts?.options),
  ) as {
    markdown: string;
    matched_node_ids: number[];
    matched_chunk_ids: number[];
    node_ids: number[];
    output_rel_path: string;
  };
}

export function writeReconstructedSkill(
  catalogDir: string,
  index: SkillsIndexDict,
  docId: string,
  opts?: {
    lineNumSpecs?: string[];
    nodeIdSpecs?: string[];
    chunkIdSpecs?: string[];
    options?: ReconstructOptions;
  },
): string {
  return writeReconstructedSkillNative(
    catalogDir,
    index,
    docId,
    opts?.lineNumSpecs,
    opts?.nodeIdSpecs,
    opts?.chunkIdSpecs,
    toNativeReconstructOptions(opts?.options),
  );
}

export function parseSkillNodeIds(spec: string): number[] {
  return parseSkillNodeIdsNative(spec);
}

export function parseSkillChunkIds(spec: string): number[] {
  return parseSkillChunkIdsNative(spec);
}

export class SkillsBuilder {
  private inner: InstanceType<typeof SkillsBuilderNative>;

  constructor(options?: { memoryOnly?: boolean; outputDir?: string }) {
    this.inner = new SkillsBuilderNative(
      options?.memoryOnly ?? true,
      options?.outputDir,
    );
  }

  buildFromDirs(
    skillDirs: string[],
    config?: PageIndexConfigInput,
  ): SkillsIndexDict {
    return this.inner.buildFromDirs(
      skillDirs,
      resolveNativeConfig(config),
    ) as SkillsIndexDict;
  }

  writeCatalog(): SkillsIndexDict {
    return this.inner.writeCatalog() as SkillsIndexDict;
  }

  toSkillsIndexJson(): Record<string, unknown> {
    return this.inner.toSkillsIndexJson() as Record<string, unknown>;
  }

  toSkillsDict(): Record<string, unknown> {
    return this.inner.toSkillsDict() as Record<string, unknown>;
  }
}
