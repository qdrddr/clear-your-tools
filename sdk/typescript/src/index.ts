/** TypeScript SDK for cyt-indexer (Rust-backed catalog indexing). */

export {
  CatalogIndex,
  anthropicToolToCatalogEntry,
  anthropicToolsToCatalogEntries,
  buildCatalogFromTools,
  buildCatalogIndex,
  catalogToolCount,
  collectEnums,
  prepareToolEntry,
  truncateDescription,
} from "./build.js";
export {
  DecomposedCatalog,
  chunkSurvivorKey,
  loadCatalog,
  removedChunks,
  retrieveTools,
  type DecomposedCatalogDict,
  type JsonRecord,
  type PolicyContextJs,
  type PolicyOptions,
  type RemovedChunksOptions,
  type RetrieveToolsOptions,
} from "./retrieve.js";
export {
  configureRuntimeDefaults,
  decomposedScore,
  emptyOptionalFallbackK,
  enumScore,
  rerankScore,
  type RuntimeDefaultsConfig,
} from "./runtime-defaults.js";
export {
  configurePathConstants,
  decomposedPrefix,
  getRootToolKey,
  jsonExt,
  mdExt,
  toDecomposedKey,
  toolIdFromDecomposedRel,
} from "./paths.js";
export { CatalogBuilder, writeCatalogIndex } from "./catalog-io.js";
export {
  extractDocumentText,
  extractJsonCatalogDocument,
  extractLevelInfo,
  extractMdCatalogDocument,
} from "./documents.js";
export {
  SkillsBuilder,
  buildSkillsIndex,
  defaultPageIndexConfig,
  getSkillDocument,
  getSkillLineContentFromSpec,
  getSkillStructure,
  loadSkillsIndexFromDir,
  mdToTree,
  skillsIndexFromDecomposedDir,
  writeSkillsIndex,
  type PageIndexConfig,
  type SkillsIndexDict,
} from "./pageindex.js";

// Full policy surface (mirrors cyt_indexer.policies).
export * from "./policies.js";
