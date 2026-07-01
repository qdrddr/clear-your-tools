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
  bm25CohesionChunk,
  cohesionConfigToNative,
  defaultBm25CohesionConfig,
  type CohesionChunk,
} from "./bm25Cohesion.js";
export {
  bm25CatalogFingerprint,
  bm25FrontmatterGate,
  bm25ScoreCatalog,
  bm25SearchSkillChunks,
  configureBm25Defaults,
  type Bm25FrontmatterGateResult,
  type Bm25ScoreCatalogOptions,
  type Bm25SearchSkillChunksResult,
} from "./bm25Search.js";
export {
  configureTokenizerDefaults,
  countJsonTokens,
  countTokens,
} from "./tokens.js";
export {
  SkillsBuilder,
  buildSkillsIndex,
  defaultPageIndexConfig,
  getSkillContentRetrieveResult,
  getSkillDocument,
  getSkillLineContent,
  getSkillLineContentFromSpec,
  getSkillStructure,
  loadSkillsIndexFromDir,
  mdToTree,
  pageIndexConfigFromMapping,
  pageIndexConfigFromPartial,
  pageIndexConfigToNative,
  pageIndexConfigWithoutChunking,
  parseSkillChunkIds,
  parseSkillNodeIds,
  reconstructSkillMarkdown,
  skillsIndexFromDecomposedDir,
  writeReconstructedSkill,
  writeSkillsIndex,
  type Bm25CohesionConfig,
  type PageIndexConfig,
  type PageIndexConfigInput,
  type ReconstructOptions,
  type SkillsIndexDict,
} from "./pageindex.js";

// Full policy surface (mirrors cyt_indexer.policies).
export * from "./policies.js";
