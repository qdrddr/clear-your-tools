import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const native = require("../native.cjs") as typeof import("../native.d.ts");

// Build / catalog
export const getVersionNative = native.getVersion;
export const buildCatalogIndexNative = native.buildCatalogIndex;
export const buildCatalogFromToolsNative = native.buildCatalogFromTools;
export const prepareToolEntryNative = native.prepareToolEntry;
export const anthropicToolToCatalogEntryNative =
  native.anthropicToolToCatalogEntry;
export const anthropicToolsToCatalogEntriesNative =
  native.anthropicToolsToCatalogEntries;
export const truncateDescriptionNative = native.truncateDescription;
export const catalogToolCountNative = native.catalogToolCount;
export const catalogIndexToCatalogDictNative = native.catalogIndexToCatalogDict;
export const catalogIndexToolSchemaMetadataNative =
  native.catalogIndexToolSchemaMetadata;

// Retrieve
export const loadCatalogNative = native.loadCatalog;
export const retrieveCoreNative = native.retrieveCore;
export const retrieveToolsNative = native.retrieveTools;
export const chunkSurvivorKeyNative = native.chunkSurvivorKey;
export const removedChunksNative = native.removedChunks;
export const resolveBuildCatalogNative = native.resolveBuildCatalog;
export const retrieveCatalogToolCountNative = native.retrieveCatalogToolCount;
export const DecomposedCatalogNative = native.DecomposedCatalog;

// Paths
export const configurePathConstantsNative = native.configurePathConstants;
export const pathMdExtNative = native.mdExt;
export const pathJsonExtNative = native.jsonExt;
export const pathDecomposedPrefixNative = native.decomposedPrefix;
export const pathDecomposedRootNative = native.decomposedRoot;
export const pathCatalogPrefixNative = native.catalogPrefix;
export const pathDefaultCatalogDirNative = native.pathDefaultCatalogDir;
export const pathBuilderMemoryOnlyNative = native.pathBuilderMemoryOnly;
export const pathWriteCatalogPruneNative = native.pathWriteCatalogPrune;
export const toDecomposedKeyNative = native.toDecomposedKey;
export const toolIdFromDecomposedRelNative = native.toolIdFromDecomposedRel;
export const getRootToolKeyNative = native.getRootToolKey;
export const collectEnumsNative = native.collectEnums;

// Runtime defaults
export const configureRuntimeDefaultsNative = native.configureRuntimeDefaults;
export const decomposedScoreNative = native.decomposedScore;
export const enumScoreNative = native.enumScore;
export const rerankScoreNative = native.rerankScore;
export const emptyOptionalFallbackKNative = native.emptyOptionalFallbackK;
export const runtimeDefaultSystemPolicyNative =
  native.runtimeDefaultSystemPolicy;
export const runtimeDefaultMcpPolicyNative = native.runtimeDefaultMcpPolicy;

// Policies
export const toolPoliciesNative = native.toolPolicies;
export const PolicyContextNative = native.PolicyContext;
export const policyContextFromValuesNative = native.policyContextFromValues;
export const effectivePolicyNative = native.effectivePolicy;
export const toolPassThroughNative = native.toolPassThrough;
export const batchToolPassThroughNative = native.batchToolPassThrough;
export const partitionCatalogNative = native.partitionCatalog;
export const mergeCatalogNative = native.mergeCatalog;
export const catalogNeedsPartitionNative = native.catalogNeedsPartition;
export const catalogNeedsPrunedRecomposeNative =
  native.catalogNeedsPrunedRecompose;
export const requestPassThroughNative = native.requestPassThrough;
export const fullPassThroughNative = native.fullPassThrough;
export const isDecomposedToolRootChunkNative = native.isDecomposedToolRootChunk;
export const isDecomposedOptionalPropertyChunkNative =
  native.isDecomposedOptionalPropertyChunk;
export const filterRecomposeJsonEntriesNative =
  native.filterRecomposeJsonEntries;
export const mitigateEmptyOptionalPropertiesNative =
  native.mitigateEmptyOptionalProperties;
export const appendDescriptionReinstateEntriesNative =
  native.appendDescriptionReinstateEntries;
export const needsDescriptionReinstateNative = native.needsDescriptionReinstate;
export const isDescriptionPolicyNative = native.isDescriptionPolicy;
export const scoringPolicyNative = native.scoringPolicy;
export const dropRecomposedToolsWithEmptyPropertiesNative =
  native.dropRecomposedToolsWithEmptyProperties;
export const rootToolIdFromChunkNative = native.rootToolIdFromChunk;
export const isNonSystemToolIdNative = native.isNonSystemToolId;
export const isSystemToolIdNative = native.isSystemToolId;
export const chunkToolIdNative = native.chunkToolId;
export const isSystemChunkNative = native.isSystemChunk;
export const isNonSystemChunkNative = native.isNonSystemChunk;
export const isSystemRootChunkNative = native.isSystemRootChunk;
export const isMcpRootChunkNative = native.isMcpRootChunk;
export const isSystemOptionalChunkNative = native.isSystemOptionalChunk;
export const isMcpOptionalChunkNative = native.isMcpOptionalChunk;
export const stashSystemToolsNative = native.stashSystemTools;
export const restoreSystemToolsNative = native.restoreSystemTools;
export const stashMcpToolsNative = native.stashMcpTools;
export const restoreMcpToolsNative = native.restoreMcpTools;
export const mergeToolsPreservingOrderNative = native.mergeToolsPreservingOrder;
export const splitAnthropicToolsNative = native.splitAnthropicTools;
export const entriesForPolicyNative = native.entriesForPolicy;
export const toolsForCatalogNative = native.toolsForCatalog;
export const systemRequiredEnumValuesNative = native.systemRequiredEnumValues;
export const mcpRequiredEnumValuesNative = native.mcpRequiredEnumValues;
export const requiredEnumValuesByToolNative = native.requiredEnumValuesByTool;
export const optionalLeafSurvivedRerankNative =
  native.optionalLeafSurvivedRerank;
export const needsPartitionNative = native.needsPartition;
export const needsPrunedRecomposeNative = native.needsPrunedRecompose;
export const systemToolsPassThroughNative = native.systemToolsPassThrough;
export const mcpToolsPassThroughNative = native.mcpToolsPassThrough;
export const anthropicToolIsSystemNative = native.anthropicToolIsSystem;
export const anthropicToolIsMcpNative = native.anthropicToolIsMcp;
export const directRootOptionalChunksForToolNative =
  native.directRootOptionalChunksForTool;
export const rootChunkPropertiesEmptyNative = native.rootChunkPropertiesEmpty;
export const toolIdHasEmptyDecomposedRootNative =
  native.toolIdHasEmptyDecomposedRoot;
export const toolIdHadEmptyOriginalRootPropertiesNative =
  native.toolIdHadEmptyOriginalRootProperties;
export const isDirectRootOptionalPropertyChunkNative =
  native.isDirectRootOptionalPropertyChunk;
export const classifyOptionalChunksBatchNative =
  native.classifyOptionalChunksBatch;

// Catalog I/O
export const writeCatalogIndexNative = native.writeCatalogIndex;
export const CatalogBuilderNative = native.CatalogBuilder;

// Documents
export const extractJsonCatalogDocumentNative =
  native.extractJsonCatalogDocument;
export const extractMdCatalogDocumentNative = native.extractMdCatalogDocument;
export const extractDocumentTextNative = native.extractDocumentText;
export const extractLevelInfoNative = native.extractLevelInfo;

// Skills pageindex
export const buildSkillsIndexNative = native.buildSkillsIndexNapi;
export const writeSkillsIndexNative = native.writeSkillsIndexNapi;
export const loadSkillsIndexFromDirNative = native.loadSkillsIndexFromDirNapi;
export const skillsIndexFromDecomposedDirNative =
  native.skillsIndexFromDecomposedDirNapi;
export const repairSkillChunksNative = native.repairSkillChunks;
export const buildPageIndexOnlyNative = native.buildPageIndexOnly;
export const buildChunkVariantNative = native.buildChunkVariant;
export const pageIndexValidNative = native.pageIndexValid;
export const chunkVariantValidNative = native.chunkVariantValid;
export const repairSkillVariantChunksNative = native.repairSkillVariantChunks;
export const loadSkillsIndexFromEntryNative = native.loadSkillsIndexFromEntry;
export const loadMergedSkillDocumentJsonNative =
  native.loadMergedSkillDocumentJson;
export const finalizeSkillDocumentJsonNative = native.finalizeSkillDocumentJson;
export const updateSkillDocumentSourcePathNative =
  native.updateSkillDocumentSourcePath;
export const mdToTreeNative = native.mdToTreeNapi;
export const getSkillDocumentNative = native.getSkillDocumentNapi;
export const getSkillStructureNative = native.getSkillStructureNapi;
export const getSkillLineContentFromSpecNative =
  native.getSkillLineContentFromSpecNapi;
export const getSkillLineContentNative = native.getSkillLineContentNapi;
export const getSkillContentRetrieveResultNative =
  native.getSkillContentRetrieveResultNapi;
export const reconstructSkillMarkdownNative =
  native.reconstructSkillMarkdownNapi;
export const writeReconstructedSkillNative = native.writeReconstructedSkillNapi;
export const parseSkillNodeIdsNative = native.parseSkillNodeIdsNapi;
export const parseSkillChunkIdsNative = native.parseSkillChunkIdsNapi;
export const tokenCountFromDecomposedFrontmatterNative =
  native.tokenCountFromDecomposedFrontmatterNapi;
export const SkillsBuilderNative = native.SkillsBuilderNapi;
export const bm25CohesionChunkNative = native.bm25CohesionChunkNapi;

// Tokens
export const countTokensNative = native.countTokens;
export const countTokensBatchNative = native.countTokensBatch;
export const countJsonTokensNative = native.countJsonTokens;
export const configureTokenizerDefaultsNative =
  native.configureTokenizerDefaults;

// Cache
export const toolsCatalogContentHashNative = native.toolsCatalogContentHash;
export const ensureToolCatalogNative = native.ensureToolCatalog;
export const ensureToolCatalogFromEntriesNative =
  native.ensureToolCatalogFromEntries;
export const ensureSkillsRegistryNative = native.ensureSkillsRegistry;
export const configureMemoryCacheNative = native.configureMemoryCache;

// BM25 search
export const configureBm25DefaultsNative = native.configureBm25Defaults;
export const bm25CatalogFingerprintNative = native.bm25CatalogFingerprint;
export const bm25ScoreCatalogNative = native.bm25ScoreCatalog;
export const bm25FrontmatterGateNative = native.bm25FrontmatterGate;
export const bm25SearchSkillChunksNative = native.bm25SearchSkillChunks;
export const expSimilarityNative = native.expSimilarity;
export const batchReconstructSkillMatchesNative =
  native.batchReconstructSkillMatches;
export const greedySelectSkillItemsNative = native.greedySelectSkillItems;

// Pipeline
export const coordinateBm25PruneNative = native.coordinateBm25Prune;
export const pruneCatalogBm25AndRetrieveNative =
  native.pruneCatalogBm25AndRetrieve;
export const recomposeAndRetrieveToolsNative =
  native.recomposeAndRetrieveTools;
export const classifyAndCountCatalogNative = native.classifyAndCountCatalog;
export const searchSkillsAndSelectNative = native.searchSkillsAndSelect;
export const buildSkillNodeCatalogNative = native.buildSkillNodeCatalog;

export type {
  PolicyOptions,
  PolicyContextJs,
  ReconstructOptionsNapi,
} from "../native.d.ts";
