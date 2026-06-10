/** Skills pageindex (markdown tree indexing and retrieval). */

import {
  SkillsBuilderNative,
  buildSkillsIndexNative,
  getSkillDocumentNative,
  getSkillLineContentFromSpecNative,
  getSkillStructureNative,
  loadSkillsIndexFromDirNative,
  mdToTreeNative,
  skillsIndexFromDecomposedDirNative,
  writeSkillsIndexNative,
} from "./native.js";

export interface PageIndexConfig {
  ifAddNodeId?: boolean;
  ifAddNodeText?: boolean;
}

export interface SkillsIndexDict {
  documents: Record<string, unknown>;
  files: Record<string, string>;
}

export function defaultPageIndexConfig(): PageIndexConfig {
  return { ifAddNodeId: true, ifAddNodeText: false };
}

function toNativeConfig(
  config?: PageIndexConfig,
): Record<string, boolean> | undefined {
  if (!config) return undefined;
  return {
    if_add_node_id: config.ifAddNodeId ?? true,
    if_add_node_text: config.ifAddNodeText ?? false,
  };
}

export function buildSkillsIndex(
  skillDirs: string[],
  config?: PageIndexConfig,
): SkillsIndexDict {
  return buildSkillsIndexNative(
    skillDirs,
    toNativeConfig(config),
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
  config?: PageIndexConfig,
): Record<string, unknown> {
  return mdToTreeNative(
    markdownContent,
    sourcePath,
    toNativeConfig(config),
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
): Array<{ line_num: number; node_id: string; content: string }> {
  return getSkillLineContentFromSpecNative(index, docId, lineNumSpec) as Array<{
    line_num: number;
    node_id: string;
    content: string;
  }>;
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
    config?: PageIndexConfig,
  ): SkillsIndexDict {
    return this.inner.buildFromDirs(
      skillDirs,
      toNativeConfig(config),
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
