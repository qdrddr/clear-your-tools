package cytindexer

//go:generate go run ./cmd/cyt-native-ensure

/*
#cgo CFLAGS: -I${SRCDIR}/../c/include
#cgo linux,amd64 LDFLAGS: -L${SRCDIR}/native/x86_64-unknown-linux-gnu -L${SRCDIR}/../../target/x86_64-unknown-linux-gnu/release -lcyt_indexer -lm -ldl -pthread
#cgo linux,arm64 LDFLAGS: -L${SRCDIR}/native/aarch64-unknown-linux-gnu -L${SRCDIR}/../../target/aarch64-unknown-linux-gnu/release -lcyt_indexer -lm -ldl -pthread
#cgo darwin,amd64 LDFLAGS: -L${SRCDIR}/native/x86_64-apple-darwin -L${SRCDIR}/../../target/x86_64-apple-darwin/release -lcyt_indexer -framework Security -lpthread
#cgo darwin,arm64 LDFLAGS: -L${SRCDIR}/native/aarch64-apple-darwin -L${SRCDIR}/../../target/aarch64-apple-darwin/release -lcyt_indexer -framework Security -lpthread
#cgo windows,amd64 LDFLAGS: -L${SRCDIR}/native/x86_64-pc-windows-msvc -L${SRCDIR}/../../target/x86_64-pc-windows-msvc/release -lcyt_indexer
#cgo windows,arm64 LDFLAGS: -L${SRCDIR}/native/aarch64-pc-windows-msvc -L${SRCDIR}/../../target/aarch64-pc-windows-msvc/release -lcyt_indexer
#ifdef index
#undef index
#endif
#include "cyt_indexer.h"
#include <stdlib.h>
*/
import "C"

import (
	"errors"
	"fmt"
	"unsafe"
)

const ok = 0

type catalogBuilderHandle struct {
	h *C.CYT_CytCatalogBuilder
}

type decomposedCatalogHandle struct {
	h *C.CYT_CytDecomposedCatalog
}

type skillsBuilderHandle struct {
	h *C.CYT_CytSkillsBuilder
}

func lastError() error {
	msg := C.cyt_get_last_error()
	if msg == nil {
		return errors.New("cyt-indexer error")
	}
	return errors.New(C.GoString(msg))
}

func cString(s string) *C.char {
	return C.CString(s)
}

func freeCString(s *C.char) {
	C.free(unsafe.Pointer(s))
}

func takeJSON(out **C.char) (string, error) {
	if out == nil {
		return "", errors.New("null out pointer")
	}
	ptr := *out
	*out = nil
	if ptr == nil {
		return "", errors.New("null JSON output")
	}
	defer C.cyt_free_string(ptr)
	return C.GoString(ptr), nil
}

func fmtBoolQuery(name string, code C.int) (bool, error) {
	if code < 0 {
		return false, fmt.Errorf("%s: %w", name, lastError())
	}
	return code != 0, nil
}

func cgoBoolFromOutInt(name string, fn func(out *C.int) C.int) (bool, error) {
	var out C.int
	if fn(&out) != ok {
		return false, lastError()
	}
	return out != 0, nil
}

func cgoClearError() {
	C.cyt_clear_error()
}

func cgoGetLastError() string {
	msg := C.cyt_get_last_error()
	if msg == nil {
		return ""
	}
	return C.GoString(msg)
}

func cgoGetVersion() (string, error) {
	var out *C.char
	if C.cyt_get_version(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogToolCount(dataJSON string) (int64, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	count := C.cyt_catalog_tool_count(cData)
	if count < 0 {
		return 0, lastError()
	}
	return int64(count), nil
}

func cgoBuildCatalogIndex(toolsJSON, enumsJSON string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	cEnums := cString(enumsJSON)
	defer freeCString(cEnums)
	var out *C.char
	if C.cyt_build_catalog_index(cTools, cEnums, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoAnthropicToolsToCatalogEntries(toolsJSON string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	var out *C.char
	if C.cyt_anthropic_tools_to_catalog_entries(cTools, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBuildCatalogFromTools(toolsJSON string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	var out *C.char
	if C.cyt_build_catalog_from_tools(cTools, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPrepareToolEntry(serverName, name, description, inputSchemaJSON string) (string, error) {
	cServer := cString(serverName)
	defer freeCString(cServer)
	cName := cString(name)
	defer freeCString(cName)
	cDesc := cString(description)
	defer freeCString(cDesc)
	cSchema := cString(inputSchemaJSON)
	defer freeCString(cSchema)
	var out *C.char
	if C.cyt_prepare_tool_entry(cServer, cName, cDesc, cSchema, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoAnthropicToolToCatalogEntry(toolJSON string) (string, error) {
	cTool := cString(toolJSON)
	defer freeCString(cTool)
	var out *C.char
	if C.cyt_anthropic_tool_to_catalog_entry(cTool, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoTruncateDescription(description string, maxTokens uint64) (string, error) {
	cDesc := cString(description)
	defer freeCString(cDesc)
	tokens, err := truncateMaxTokensArg(maxTokens)
	if err != nil {
		return "", err
	}
	var out *C.char
	if C.cyt_truncate_description(cDesc, C.ulong(tokens), &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCountTokens(text string) (int64, error) {
	cText := cString(text)
	defer freeCString(cText)
	count := C.cyt_count_tokens(cText)
	if count < 0 {
		return 0, lastError()
	}
	return int64(count), nil
}

func cgoCountJsonTokens(jsonStr string) (int64, error) {
	cJSON := cString(jsonStr)
	defer freeCString(cJSON)
	count := C.cyt_count_json_tokens(cJSON)
	if count < 0 {
		return 0, lastError()
	}
	return int64(count), nil
}

func cgoConfigureTokenizerDefaults(configJSON string) error {
	var cCfg *C.char
	if configJSON != "" {
		cCfg = cString(configJSON)
		defer freeCString(cCfg)
	}
	if C.cyt_configure_tokenizer_defaults(cCfg) != ok {
		return lastError()
	}
	return nil
}

func cgoConfigureBm25Defaults(configJSON string) error {
	var cCfg *C.char
	if configJSON != "" {
		cCfg = cString(configJSON)
		defer freeCString(cCfg)
	}
	if C.cyt_configure_bm25_defaults(cCfg) != ok {
		return lastError()
	}
	return nil
}

func cgoBm25CatalogFingerprint(dataJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	var out *C.char
	if C.cyt_bm25_catalog_fingerprint(cData, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBm25ScoreCatalog(dataJSON, query, optionsJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cQuery := cString(query)
	defer freeCString(cQuery)
	var cOpts *C.char
	if optionsJSON != "" {
		cOpts = cString(optionsJSON)
		defer freeCString(cOpts)
	}
	var out *C.char
	if C.cyt_bm25_score_catalog(cData, cQuery, cOpts, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBm25FrontmatterGate(entriesJSON, query string, upperLimit float64) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cQuery := cString(query)
	defer freeCString(cQuery)
	var out *C.char
	if C.cyt_bm25_frontmatter_gate(cEntries, cQuery, C.double(upperLimit), &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBm25SearchSkillChunks(entriesJSON, query string, threshold float64, excludedJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cQuery := cString(query)
	defer freeCString(cQuery)
	var cExcluded *C.char
	if excludedJSON != "" {
		cExcluded = cString(excludedJSON)
		defer freeCString(cExcluded)
	}
	var out *C.char
	if C.cyt_bm25_search_skill_chunks(cEntries, cQuery, C.double(threshold), cExcluded, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoExpSimilarity(raw float64) float64 {
	return float64(C.cyt_exp_similarity(C.double(raw)))
}

func cgoBatchReconstructSkillMatches(groupsJSON string) (string, error) {
	cGroups := cString(groupsJSON)
	defer freeCString(cGroups)
	var out *C.char
	if C.cyt_batch_reconstruct_skill_matches(cGroups, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGreedySelectSkillItems(survivorsJSON, itemKind string, maxTokens int64) (string, error) {
	cSurvivors := cString(survivorsJSON)
	defer freeCString(cSurvivors)
	cKind := cString(itemKind)
	defer freeCString(cKind)
	var out *C.char
	if C.cyt_greedy_select_skill_items(cSurvivors, cKind, C.int64_t(maxTokens), &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoWriteCatalogIndex(indexJSON, outputDir string, prune bool) error {
	cIndex := cString(indexJSON)
	defer freeCString(cIndex)
	cDir := cString(outputDir)
	defer freeCString(cDir)
	pruneFlag := C.int(0)
	if prune {
		pruneFlag = 1
	}
	if C.cyt_write_catalog_index(cIndex, cDir, pruneFlag) != ok {
		return lastError()
	}
	return nil
}

func cgoCatalogIndexToCatalogDict(indexJSON, catalogPrefix string) (string, error) {
	cIndex := cString(indexJSON)
	defer freeCString(cIndex)
	cPrefix := cString(catalogPrefix)
	defer freeCString(cPrefix)
	var out *C.char
	if C.cyt_catalog_index_to_catalog_dict(cIndex, cPrefix, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogIndexToolSchemaMetadata(indexJSON string) (string, error) {
	cIndex := cString(indexJSON)
	defer freeCString(cIndex)
	var out *C.char
	if C.cyt_catalog_index_tool_schema_metadata(cIndex, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogBuilderNew(memoryOnly bool, outputDir string) (catalogBuilderHandle, error) {
	cDir := cString(outputDir)
	defer freeCString(cDir)
	mem := C.int(0)
	if memoryOnly {
		mem = 1
	}
	var handle *C.CYT_CytCatalogBuilder
	if C.cyt_catalog_builder_new(mem, cDir, &handle) != ok {
		return catalogBuilderHandle{}, lastError()
	}
	return catalogBuilderHandle{h: handle}, nil
}

func cgoCatalogBuilderFree(h catalogBuilderHandle) {
	if h.h != nil {
		C.cyt_catalog_builder_free(h.h)
	}
}

func cgoCatalogBuilderAddTool(h catalogBuilderHandle, entryJSON string) error {
	cEntry := cString(entryJSON)
	defer freeCString(cEntry)
	if C.cyt_catalog_builder_add_tool(h.h, cEntry) != ok {
		return lastError()
	}
	return nil
}

func cgoCatalogBuilderGetToolInfo(h catalogBuilderHandle, serverName, toolName string) (string, error) {
	cServer := cString(serverName)
	defer freeCString(cServer)
	cTool := cString(toolName)
	defer freeCString(cTool)
	var out *C.char
	if C.cyt_catalog_builder_get_tool_info(h.h, cServer, cTool, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogBuilderBuildIndex(h catalogBuilderHandle) (string, error) {
	var out *C.char
	if C.cyt_catalog_builder_build_index(h.h, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogBuilderWriteCatalog(h catalogBuilderHandle) (string, error) {
	var out *C.char
	if C.cyt_catalog_builder_write_catalog(h.h, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogBuilderToCatalogDict(h catalogBuilderHandle, catalogPrefix string) (string, error) {
	cPrefix := cString(catalogPrefix)
	defer freeCString(cPrefix)
	var out *C.char
	if C.cyt_catalog_builder_to_catalog_dict(h.h, cPrefix, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoDecomposedCatalogNew() (decomposedCatalogHandle, error) {
	var handle *C.CYT_CytDecomposedCatalog
	if C.cyt_decomposed_catalog_new(&handle) != ok {
		return decomposedCatalogHandle{}, lastError()
	}
	return decomposedCatalogHandle{h: handle}, nil
}

func cgoDecomposedCatalogFromCatalogIndex(indexJSON string) (decomposedCatalogHandle, error) {
	cIndex := cString(indexJSON)
	defer freeCString(cIndex)
	var handle *C.CYT_CytDecomposedCatalog
	if C.cyt_decomposed_catalog_from_catalog_index(cIndex, &handle) != ok {
		return decomposedCatalogHandle{}, lastError()
	}
	return decomposedCatalogHandle{h: handle}, nil
}

func cgoDecomposedCatalogFromCatalogDict(dataJSON string) (decomposedCatalogHandle, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	var handle *C.CYT_CytDecomposedCatalog
	if C.cyt_decomposed_catalog_from_catalog_dict(cData, &handle) != ok {
		return decomposedCatalogHandle{}, lastError()
	}
	return decomposedCatalogHandle{h: handle}, nil
}

func cgoDecomposedCatalogFree(h decomposedCatalogHandle) {
	if h.h != nil {
		C.cyt_decomposed_catalog_free(h.h)
	}
}

func cgoDecomposedCatalogHasJSON(h decomposedCatalogHandle, key string) (bool, error) {
	cKey := cString(key)
	defer freeCString(cKey)
	return fmtBoolQuery("HasJSON", C.cyt_decomposed_catalog_has_json(h.h, cKey))
}

func cgoDecomposedCatalogGetJSON(h decomposedCatalogHandle, key string) (string, error) {
	cKey := cString(key)
	defer freeCString(cKey)
	var out *C.char
	if C.cyt_decomposed_catalog_get_json(h.h, cKey, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoLoadCatalog(dirPath string) (string, error) {
	cDir := cString(dirPath)
	defer freeCString(cDir)
	var out *C.char
	if C.cyt_load_catalog(cDir, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRetrieveTools(dataJSON string, catalog decomposedCatalogHandle, catalogIndexJSON string, applyDecomposedScoreFilter bool, preserveValuesJSON, ctxJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cPreserve := cString(preserveValuesJSON)
	defer freeCString(cPreserve)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	filter := C.int(0)
	if applyDecomposedScoreFilter {
		filter = 1
	}
	var out *C.char
	if C.cyt_retrieve_tools(cData, catalog.h, cIndex, filter, cPreserve, cCtx, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRetrieveCore(dataJSON, storeJSON, survivorJSON string, applyDecomposedScoreFilter bool, policyOptionsJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cStore := cString(storeJSON)
	defer freeCString(cStore)
	cSurvivor := cString(survivorJSON)
	defer freeCString(cSurvivor)
	cPolicy := cString(policyOptionsJSON)
	defer freeCString(cPolicy)
	filter := C.int(0)
	if applyDecomposedScoreFilter {
		filter = 1
	}
	var out *C.char
	if C.cyt_retrieve_core(cData, cStore, cSurvivor, filter, cPolicy, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoChunkSurvivorKey(itemJSON, section string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	cSection := cString(section)
	defer freeCString(cSection)
	var out *C.char
	if C.cyt_chunk_survivor_key(cItem, cSection, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRemovedChunks(fullCatalogJSON, survivingJSON string, applyDecomposedScoreFilter bool) (string, error) {
	cFull := cString(fullCatalogJSON)
	defer freeCString(cFull)
	cSurviving := cString(survivingJSON)
	defer freeCString(cSurviving)
	filter := C.int(0)
	if applyDecomposedScoreFilter {
		filter = 1
	}
	var out *C.char
	if C.cyt_removed_chunks(cFull, cSurviving, filter, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRetrieveCatalogToolCount(dataJSON string) (int64, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	count := C.cyt_retrieve_catalog_tool_count(cData)
	if count < 0 {
		return 0, lastError()
	}
	return int64(count), nil
}

func cgoResolveBuildCatalog(catalogJSON, survivorJSON string) (string, error) {
	cCatalog := cString(catalogJSON)
	defer freeCString(cCatalog)
	cSurvivor := cString(survivorJSON)
	defer freeCString(cSurvivor)
	var out *C.char
	if C.cyt_resolve_build_catalog(cCatalog, cSurvivor, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCollectEnums(schemaJSON string) (string, error) {
	cSchema := cString(schemaJSON)
	defer freeCString(cSchema)
	var out *C.char
	if C.cyt_collect_enums(cSchema, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToDecomposedKey(filePath string) (string, error) {
	cPath := cString(filePath)
	defer freeCString(cPath)
	var out *C.char
	if C.cyt_to_decomposed_key(cPath, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToolIDFromDecomposedRel(relPath string) (string, error) {
	cPath := cString(relPath)
	defer freeCString(cPath)
	var out *C.char
	if C.cyt_tool_id_from_decomposed_rel(cPath, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGetRootToolKey(filePath string) (string, error) {
	cPath := cString(filePath)
	defer freeCString(cPath)
	var out *C.char
	if C.cyt_get_root_tool_key(cPath, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoConfigureRuntimeDefaults(decomposedScore, enumScore, rerankScore float64, emptyOptionalFallbackK uint64, defaultSystemPolicy, defaultMcpPolicy string) error {
	cSystem := cString(defaultSystemPolicy)
	defer freeCString(cSystem)
	cMcp := cString(defaultMcpPolicy)
	defer freeCString(cMcp)
	if C.cyt_configure_runtime_defaults(
		C.double(decomposedScore),
		C.double(enumScore),
		C.double(rerankScore),
		C.uintptr_t(emptyOptionalFallbackK),
		cSystem,
		cMcp,
	) != ok {
		return lastError()
	}
	return nil
}

func cgoRuntimeDecomposedScore() float64 {
	return float64(C.cyt_runtime_decomposed_score())
}

func cgoRuntimeEnumScore() float64 {
	return float64(C.cyt_runtime_enum_score())
}

func cgoRuntimeRerankScore() float64 {
	return float64(C.cyt_runtime_rerank_score())
}

func cgoRuntimeEmptyOptionalFallbackK() uint64 {
	return uint64(C.cyt_runtime_empty_optional_fallback_k())
}

func cgoRuntimeDefaultSystemPolicy() (string, error) {
	var out *C.char
	if C.cyt_runtime_default_system_policy(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRuntimeDefaultMcpPolicy() (string, error) {
	var out *C.char
	if C.cyt_runtime_default_mcp_policy(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToolPolicies() (string, error) {
	var out *C.char
	if C.cyt_tool_policies(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPolicyContextFromValues(configJSON string) (string, error) {
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_policy_context_from_values(cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEffectivePolicy(ctxJSON, toolID string) (string, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cTool := cString(toolID)
	defer freeCString(cTool)
	var out *C.char
	if C.cyt_effective_policy(cCtx, cTool, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBatchToolPassThrough(ctxJSON, toolIDsJSON string) (string, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cIDs := cString(toolIDsJSON)
	defer freeCString(cIDs)
	var out *C.char
	if C.cyt_batch_tool_pass_through(cCtx, cIDs, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPruneCatalogBm25AndRetrieve(
	catalogJSON, buildCatalogJSON, catalogIndexJSON, query, scoringCtxJSON, outputCtxJSON, optionsJSON string,
) (string, error) {
	cCatalog := cString(catalogJSON)
	defer freeCString(cCatalog)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cQuery := cString(query)
	defer freeCString(cQuery)
	cScoring := cString(scoringCtxJSON)
	defer freeCString(cScoring)
	cOutput := cString(outputCtxJSON)
	defer freeCString(cOutput)
	var cOptions *C.char
	if optionsJSON != "" {
		cOptions = cString(optionsJSON)
		defer freeCString(cOptions)
	}
	var out *C.char
	if C.cyt_prune_catalog_bm25_and_retrieve(
		cCatalog, cBuild, cIndex, cQuery, cScoring, cOutput, cOptions, &out,
	) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRecomposeAndRetrieveTools(
	dataJSON, buildCatalogJSON, catalogIndexJSON string,
	postRerankJSON, postRerankScoredJSON, pinnedJSON string,
	pipelineJSON, scoringCtxJSON, outputCtxJSON string,
) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	var cPostRerank *C.char
	if postRerankJSON != "" {
		cPostRerank = cString(postRerankJSON)
		defer freeCString(cPostRerank)
	}
	var cPostRerankScored *C.char
	if postRerankScoredJSON != "" {
		cPostRerankScored = cString(postRerankScoredJSON)
		defer freeCString(cPostRerankScored)
	}
	var cPinned *C.char
	if pinnedJSON != "" {
		cPinned = cString(pinnedJSON)
		defer freeCString(cPinned)
	}
	cPipeline := cString(pipelineJSON)
	defer freeCString(cPipeline)
	cScoring := cString(scoringCtxJSON)
	defer freeCString(cScoring)
	cOutput := cString(outputCtxJSON)
	defer freeCString(cOutput)
	var out *C.char
	if C.cyt_recompose_and_retrieve_tools(
		cData, cBuild, cIndex, cPostRerank, cPostRerankScored, cPinned, cPipeline, cScoring, cOutput, &out,
	) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoClassifyAndCountCatalog(catalogJSON, toolsJSON string) (string, error) {
	cCatalog := cString(catalogJSON)
	defer freeCString(cCatalog)
	var cTools *C.char
	if toolsJSON != "" {
		cTools = cString(toolsJSON)
		defer freeCString(cTools)
	}
	var out *C.char
	if C.cyt_classify_and_count_catalog(cCatalog, cTools, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSearchSkillsAndSelect(entriesJSON, query, optionsJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cQuery := cString(query)
	defer freeCString(cQuery)
	var cOptions *C.char
	if optionsJSON != "" {
		cOptions = cString(optionsJSON)
		defer freeCString(cOptions)
	}
	var out *C.char
	if C.cyt_search_skills_and_select(cEntries, cQuery, cOptions, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBuildSkillNodeCatalog(entriesJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	var out *C.char
	if C.cyt_build_skill_node_catalog(cEntries, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCoordinateBm25Prune(
	skillsEntriesJSON, catalogJSON, buildCatalogJSON, catalogIndexJSON, query, scoringCtxJSON, outputCtxJSON, optionsJSON string,
) (string, error) {
	cSkills := cString(skillsEntriesJSON)
	defer freeCString(cSkills)
	cCatalog := cString(catalogJSON)
	defer freeCString(cCatalog)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cQuery := cString(query)
	defer freeCString(cQuery)
	cScoring := cString(scoringCtxJSON)
	defer freeCString(cScoring)
	cOutput := cString(outputCtxJSON)
	defer freeCString(cOutput)
	var cOptions *C.char
	if optionsJSON != "" {
		cOptions = cString(optionsJSON)
		defer freeCString(cOptions)
	}
	var out *C.char
	if C.cyt_coordinate_bm25_prune(
		cSkills, cCatalog, cBuild, cIndex, cQuery, cScoring, cOutput, cOptions, &out,
	) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPartitionCatalog(dataJSON, ctxJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	var out *C.char
	if C.cyt_partition_catalog(cData, cCtx, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoMergeCatalog(processedJSON, pinnedJSON string) (string, error) {
	cProcessed := cString(processedJSON)
	defer freeCString(cProcessed)
	cPinned := cString(pinnedJSON)
	defer freeCString(cPinned)
	var out *C.char
	if C.cyt_merge_catalog(cProcessed, cPinned, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCatalogNeedsPartition(dataJSON, ctxJSON string) (bool, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("CatalogNeedsPartition", C.cyt_catalog_needs_partition(cData, cCtx))
}

func cgoCatalogNeedsPrunedRecompose(dataJSON, ctxJSON string) (bool, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("CatalogNeedsPrunedRecompose", C.cyt_catalog_needs_pruned_recompose(cData, cCtx))
}

func cgoRequestPassThrough(ctxJSON, toolsJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	return fmtBoolQuery("RequestPassThrough", C.cyt_request_pass_through(cCtx, cTools))
}

func cgoFilterRecomposeJSONEntries(jsonListJSON, ctxJSON string, rerankScore float64, useDefaultRerankScore bool, llmSelectedPathsJSON string) (string, error) {
	cList := cString(jsonListJSON)
	defer freeCString(cList)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cPaths := cString(llmSelectedPathsJSON)
	defer freeCString(cPaths)
	useDefault := C.int(0)
	if useDefaultRerankScore {
		useDefault = 1
	}
	var out *C.char
	if C.cyt_filter_recompose_json_entries(cList, cCtx, C.double(rerankScore), useDefault, cPaths, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoMitigateEmptyOptionalProperties(entriesJSON, catalogIndexJSON, ctxJSON, postRerankScoredJSON, pipelineJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cScored := cString(postRerankScoredJSON)
	defer freeCString(cScored)
	cPipeline := cString(pipelineJSON)
	defer freeCString(cPipeline)
	var out *C.char
	if C.cyt_mitigate_empty_optional_properties(cEntries, cIndex, cCtx, cScored, cPipeline, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEnsureRootJSONForSurvivingTools(entriesJSON, buildCatalogJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	var out *C.char
	if C.cyt_ensure_root_json_for_surviving_tools(cEntries, cBuild, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoJSONEntriesForRecompose(dataJSON, pinnedJSON, buildCatalogJSON, postRerankScoredJSON, ctxJSON, catalogIndexJSON, pipelineJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	cPinned := cString(pinnedJSON)
	defer freeCString(cPinned)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	cScored := cString(postRerankScoredJSON)
	defer freeCString(cScored)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cPipeline := cString(pipelineJSON)
	defer freeCString(cPipeline)
	var out *C.char
	if C.cyt_json_entries_for_recompose(cData, cPinned, cBuild, cScored, cCtx, cIndex, cPipeline, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoAppendDescriptionReinstateEntries(entriesJSON, buildCatalogJSON, catalogIndexJSON, ctxJSON string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cBuild := cString(buildCatalogJSON)
	defer freeCString(cBuild)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	var out *C.char
	if C.cyt_append_description_reinstate_entries(cEntries, cBuild, cIndex, cCtx, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoIsDescriptionPolicy(policy string) (bool, error) {
	cPolicy := cString(policy)
	defer freeCString(cPolicy)
	return fmtBoolQuery("IsDescriptionPolicy", C.cyt_is_description_policy(cPolicy))
}

func cgoScoringPolicy(policy string) (string, error) {
	cPolicy := cString(policy)
	defer freeCString(cPolicy)
	var out *C.char
	if C.cyt_scoring_policy(cPolicy, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoDropRecomposedToolsWithEmptyProperties(toolsJSON, catalogIndexJSON, ctxJSON string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	var out *C.char
	if C.cyt_drop_recomposed_tools_with_empty_properties(cTools, cIndex, cCtx, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRootToolIDFromChunk(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_root_tool_id_from_chunk(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoChunkToolID(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_chunk_tool_id(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoIsNonSystemToolID(toolID string) (bool, error) {
	cID := cString(toolID)
	defer freeCString(cID)
	return fmtBoolQuery("IsNonSystemToolID", C.cyt_is_non_system_tool_id(cID))
}

func cgoIsSystemToolID(toolID string) (bool, error) {
	cID := cString(toolID)
	defer freeCString(cID)
	return fmtBoolQuery("IsSystemToolID", C.cyt_is_system_tool_id(cID))
}

func cgoMergeToolsPreservingOrder(originalJSON, prunedByNameJSON, stashedByNameJSON string) (string, error) {
	cOrig := cString(originalJSON)
	defer freeCString(cOrig)
	cPruned := cString(prunedByNameJSON)
	defer freeCString(cPruned)
	cStashed := cString(stashedByNameJSON)
	defer freeCString(cStashed)
	var out *C.char
	if C.cyt_merge_tools_preserving_order(cOrig, cPruned, cStashed, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSplitAnthropicTools(toolsJSON string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	var out *C.char
	if C.cyt_split_anthropic_tools(cTools, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEntriesForPolicy(ctxJSON, allEntriesJSON string) (string, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cEntries := cString(allEntriesJSON)
	defer freeCString(cEntries)
	var out *C.char
	if C.cyt_entries_for_policy(cCtx, cEntries, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToolsForCatalog(ctxJSON, toolsJSON string) (string, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	var out *C.char
	if C.cyt_tools_for_catalog(cCtx, cTools, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSystemRequiredEnumValues(dataJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	var out *C.char
	if C.cyt_system_required_enum_values(cData, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoMcpRequiredEnumValues(dataJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	var out *C.char
	if C.cyt_mcp_required_enum_values(cData, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRequiredEnumValuesByTool(dataJSON string) (string, error) {
	cData := cString(dataJSON)
	defer freeCString(cData)
	var out *C.char
	if C.cyt_required_enum_values_by_tool(cData, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoOptionalLeafSurvivedRerank(itemJSON, ctxJSON string, rerankScore float64, useDefaultRerankScore bool, llmSelectedPathsJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cPaths := cString(llmSelectedPathsJSON)
	defer freeCString(cPaths)
	useDefault := C.int(0)
	if useDefaultRerankScore {
		useDefault = 1
	}
	return cgoBoolFromOutInt("", func(out *C.int) C.int {
		return C.cyt_optional_leaf_survived_rerank(cItem, cCtx, C.double(rerankScore), useDefault, cPaths, out)
	})
}

func cgoAnthropicToolIsSystem(toolJSON string) (bool, error) {
	cTool := cString(toolJSON)
	defer freeCString(cTool)
	return fmtBoolQuery("AnthropicToolIsSystem", C.cyt_anthropic_tool_is_system(cTool))
}

func cgoAnthropicToolIsMcp(toolJSON string) (bool, error) {
	cTool := cString(toolJSON)
	defer freeCString(cTool)
	return fmtBoolQuery("AnthropicToolIsMcp", C.cyt_anthropic_tool_is_mcp(cTool))
}

func cgoDirectRootOptionalChunksForTool(itemsJSON, toolID string) (string, error) {
	cItems := cString(itemsJSON)
	defer freeCString(cItems)
	cTool := cString(toolID)
	defer freeCString(cTool)
	var out *C.char
	if C.cyt_direct_root_optional_chunks_for_tool(cItems, cTool, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToolIDHasEmptyDecomposedRoot(catalogIndexJSON, toolID string) (bool, error) {
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cTool := cString(toolID)
	defer freeCString(cTool)
	return cgoBoolFromOutInt("", func(out *C.int) C.int {
		return C.cyt_tool_id_has_empty_decomposed_root(cIndex, cTool, out)
	})
}

func cgoToolIDHadEmptyOriginalRootProperties(catalogIndexJSON, toolID string) (bool, error) {
	cIndex := cString(catalogIndexJSON)
	defer freeCString(cIndex)
	cTool := cString(toolID)
	defer freeCString(cTool)
	return cgoBoolFromOutInt("", func(out *C.int) C.int {
		return C.cyt_tool_id_had_empty_original_root_properties(cIndex, cTool, out)
	})
}

func cgoExtractDocumentText(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_extract_document_text(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoExtractLevelInfo(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_extract_level_info(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoExtractJSONCatalogDocument(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_extract_json_catalog_document(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoExtractMdCatalogDocument(itemJSON string) (string, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	var out *C.char
	if C.cyt_extract_md_catalog_document(cItem, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBm25CohesionDefaultConfig() (string, error) {
	var out *C.char
	if C.cyt_bm25_cohesion_default_config(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBm25CohesionChunk(text, configJSON string) (string, error) {
	cText := cString(text)
	defer freeCString(cText)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_bm25_cohesion_chunk(cText, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSkillsBuilderNew(memoryOnly bool, outputDir string) (skillsBuilderHandle, error) {
	cDir := cString(outputDir)
	defer freeCString(cDir)
	mem := C.int(0)
	if memoryOnly {
		mem = 1
	}
	var handle *C.CYT_CytSkillsBuilder
	if C.cyt_skills_builder_new(mem, cDir, &handle) != ok {
		return skillsBuilderHandle{}, lastError()
	}
	return skillsBuilderHandle{h: handle}, nil
}

func cgoSkillsBuilderFree(h skillsBuilderHandle) {
	if h.h != nil {
		C.cyt_skills_builder_free(h.h)
	}
}

func cgoSkillsBuilderBuildFromDirs(h skillsBuilderHandle, skillDirsJSON, configJSON string) (string, error) {
	cDirs := cString(skillDirsJSON)
	defer freeCString(cDirs)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_skills_builder_build_from_dirs(h.h, cDirs, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSkillsBuilderWriteCatalog(h skillsBuilderHandle) (string, error) {
	var out *C.char
	if C.cyt_skills_builder_write_catalog(h.h, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSkillsBuilderToSkillsIndexJSON(h skillsBuilderHandle) (string, error) {
	var out *C.char
	if C.cyt_skills_builder_to_skills_index_json(h.h, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoSkillsBuilderToSkillsDict(h skillsBuilderHandle) (string, error) {
	var out *C.char
	if C.cyt_skills_builder_to_skills_dict(h.h, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBuildSkillsIndex(skillDirsJSON, configJSON string) (string, error) {
	cDirs := cString(skillDirsJSON)
	defer freeCString(cDirs)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_build_skills_index(cDirs, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoWriteSkillsIndex(indexJSON, outputDir string) error {
	cIndex := cString(indexJSON)
	defer freeCString(cIndex)
	cDir := cString(outputDir)
	defer freeCString(cDir)
	if C.cyt_write_skills_index(cIndex, cDir) != ok {
		return lastError()
	}
	return nil
}

func cgoLoadSkillsIndexFromDir(catalogDir string) (string, error) {
	cDir := cString(catalogDir)
	defer freeCString(cDir)
	var out *C.char
	if C.cyt_load_skills_index_from_dir(cDir, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRepairSkillChunks(entryDir, docID, configJSON string) error {
	cDir := cString(entryDir)
	defer freeCString(cDir)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	if C.cyt_repair_skill_chunks(cDir, cDoc, cCfg) != ok {
		return lastError()
	}
	return nil
}

func cgoSkillsIndexFromDecomposedDir(dir string) (string, error) {
	cDir := cString(dir)
	defer freeCString(cDir)
	var out *C.char
	if C.cyt_skills_index_from_decomposed_dir(cDir, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoMdToTree(markdownContent, sourcePath, configJSON string) (string, error) {
	cMd := cString(markdownContent)
	defer freeCString(cMd)
	cPath := cString(sourcePath)
	defer freeCString(cPath)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_md_to_tree(cMd, cPath, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGetSkillDocument(documentsJSON, docID string) (string, error) {
	cDocs := cString(documentsJSON)
	defer freeCString(cDocs)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	var out *C.char
	if C.cyt_get_skill_document(cDocs, cDoc, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGetSkillStructure(documentsJSON, docID string) (string, error) {
	cDocs := cString(documentsJSON)
	defer freeCString(cDocs)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	var out *C.char
	if C.cyt_get_skill_structure(cDocs, cDoc, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGetSkillLineContentFromSpec(indexOrDocsJSON, docID, lineNumSpec string) (string, error) {
	cIndex := cString(indexOrDocsJSON)
	defer freeCString(cIndex)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cSpec := cString(lineNumSpec)
	defer freeCString(cSpec)
	var out *C.char
	if C.cyt_get_skill_line_content_from_spec(cIndex, cDoc, cSpec, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoGetSkillContentRetrieveResult(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) (string, error) {
	cIndex := cString(indexOrDocsJSON)
	defer freeCString(cIndex)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cLines := cString(lineNumSpecsJSON)
	defer freeCString(cLines)
	cNodes := cString(nodeIDSpecsJSON)
	defer freeCString(cNodes)
	cChunks := cString(chunkIDSpecsJSON)
	defer freeCString(cChunks)
	cOpts := cString(optionsJSON)
	defer freeCString(cOpts)
	var out *C.char
	if C.cyt_get_skill_content_retrieve_result(cIndex, cDoc, cLines, cNodes, cChunks, cOpts, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoReconstructSkillMarkdown(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) (string, error) {
	cIndex := cString(indexOrDocsJSON)
	defer freeCString(cIndex)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cLines := cString(lineNumSpecsJSON)
	defer freeCString(cLines)
	cNodes := cString(nodeIDSpecsJSON)
	defer freeCString(cNodes)
	cChunks := cString(chunkIDSpecsJSON)
	defer freeCString(cChunks)
	cOpts := cString(optionsJSON)
	defer freeCString(cOpts)
	var out *C.char
	if C.cyt_reconstruct_skill_markdown(cIndex, cDoc, cLines, cNodes, cChunks, cOpts, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoWriteReconstructedSkill(catalogDir, indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) error {
	cDir := cString(catalogDir)
	defer freeCString(cDir)
	cIndex := cString(indexOrDocsJSON)
	defer freeCString(cIndex)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cLines := cString(lineNumSpecsJSON)
	defer freeCString(cLines)
	cNodes := cString(nodeIDSpecsJSON)
	defer freeCString(cNodes)
	cChunks := cString(chunkIDSpecsJSON)
	defer freeCString(cChunks)
	cOpts := cString(optionsJSON)
	defer freeCString(cOpts)
	var out *C.char
	if C.cyt_write_reconstructed_skill(cDir, cIndex, cDoc, cLines, cNodes, cChunks, cOpts, &out) != ok {
		return lastError()
	}
	_, err := takeJSON(&out)
	return err
}

func cgoGetSkillLineContent(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON string) (string, error) {
	cIndex := cString(indexOrDocsJSON)
	defer freeCString(cIndex)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cLines := cString(lineNumSpecsJSON)
	defer freeCString(cLines)
	cNodes := cString(nodeIDSpecsJSON)
	defer freeCString(cNodes)
	cChunks := cString(chunkIDSpecsJSON)
	defer freeCString(cChunks)
	var out *C.char
	if C.cyt_get_skill_line_content(cIndex, cDoc, cLines, cNodes, cChunks, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoParseSkillChunkIDs(spec string) (string, error) {
	cSpec := cString(spec)
	defer freeCString(cSpec)
	var out *C.char
	if C.cyt_parse_skill_chunk_ids(cSpec, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoParseSkillNodeIDs(spec string) (string, error) {
	cSpec := cString(spec)
	defer freeCString(cSpec)
	var out *C.char
	if C.cyt_parse_skill_node_ids(cSpec, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoTokenCountFromDecomposedFrontmatter(content string) (int64, bool, error) {
	cContent := cString(content)
	defer freeCString(cContent)
	var out C.long
	if C.cyt_token_count_from_decomposed_frontmatter(cContent, &out) != ok {
		return 0, false, lastError()
	}
	if out < 0 {
		return 0, false, nil
	}
	return int64(out), true, nil
}

func cgoReconstructOptionsDefault() (string, error) {
	var out *C.char
	if C.cyt_reconstruct_options_default(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoCountTokensBatch(textsJSON string) (string, error) {
	cTexts := cString(textsJSON)
	defer freeCString(cTexts)
	var out *C.char
	if C.cyt_count_tokens_batch(cTexts, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoClassifyOptionalChunksBatch(itemsJSON string) (string, error) {
	cItems := cString(itemsJSON)
	defer freeCString(cItems)
	var out *C.char
	if C.cyt_classify_optional_chunks_batch(cItems, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoToolsCatalogContentHash(toolsJSON, policyFingerprint string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	cFP := cString(policyFingerprint)
	defer freeCString(cFP)
	var out *C.char
	if C.cyt_tools_catalog_content_hash(cTools, cFP, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEnsureToolCatalog(toolsJSON, policyFingerprint, toolsRoot, policy string) (string, error) {
	cTools := cString(toolsJSON)
	defer freeCString(cTools)
	cFP := cString(policyFingerprint)
	defer freeCString(cFP)
	cRoot := cString(toolsRoot)
	defer freeCString(cRoot)
	var cPolicy *C.char
	if policy != "" {
		cPolicy = cString(policy)
		defer freeCString(cPolicy)
	}
	var out *C.char
	if C.cyt_ensure_tool_catalog(cTools, cFP, cRoot, cPolicy, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEnsureToolCatalogFromEntries(entriesJSON, enumsJSON, policyFingerprint, toolsRoot, policy string) (string, error) {
	cEntries := cString(entriesJSON)
	defer freeCString(cEntries)
	cEnums := cString(enumsJSON)
	defer freeCString(cEnums)
	cFP := cString(policyFingerprint)
	defer freeCString(cFP)
	cRoot := cString(toolsRoot)
	defer freeCString(cRoot)
	var cPolicy *C.char
	if policy != "" {
		cPolicy = cString(policy)
		defer freeCString(cPolicy)
	}
	var out *C.char
	if C.cyt_ensure_tool_catalog_from_entries(cEntries, cEnums, cFP, cRoot, cPolicy, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoEnsureSkillsRegistry(sourcePathsJSON, catalogRoot, pageindexConfigJSON, pipeline, indexParamsHash, policy string) (string, error) {
	cPaths := cString(sourcePathsJSON)
	defer freeCString(cPaths)
	cRoot := cString(catalogRoot)
	defer freeCString(cRoot)
	var cCfg *C.char
	if pageindexConfigJSON != "" {
		cCfg = cString(pageindexConfigJSON)
		defer freeCString(cCfg)
	}
	cPipeline := cString(pipeline)
	defer freeCString(cPipeline)
	cHash := cString(indexParamsHash)
	defer freeCString(cHash)
	var cPolicy *C.char
	if policy != "" {
		cPolicy = cString(policy)
		defer freeCString(cPolicy)
	}
	var out *C.char
	if C.cyt_ensure_skills_registry(cPaths, cRoot, cCfg, cPipeline, cHash, cPolicy, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBuildPageIndexOnly(skillDirsJSON, configJSON string) (string, error) {
	cDirs := cString(skillDirsJSON)
	defer freeCString(cDirs)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_build_page_index_only(cDirs, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoBuildChunkVariant(entryDir, docID, pipeline, paramsHash, configJSON string) (string, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cPipeline := cString(pipeline)
	defer freeCString(cPipeline)
	cHash := cString(paramsHash)
	defer freeCString(cHash)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	var out *C.char
	if C.cyt_build_chunk_variant(cEntry, cDoc, cPipeline, cHash, cCfg, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPageIndexValid(entryDir, contentSHA256 string) (bool, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cHash := cString(contentSHA256)
	defer freeCString(cHash)
	var out C.int
	if C.cyt_page_index_valid(cEntry, cHash, &out) != ok {
		return false, lastError()
	}
	return out != 0, nil
}

func cgoChunkVariantValid(entryDir, docID, pipeline, paramsHash string) (bool, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cPipeline := cString(pipeline)
	defer freeCString(cPipeline)
	cHash := cString(paramsHash)
	defer freeCString(cHash)
	var out C.int
	if C.cyt_chunk_variant_valid(cEntry, cDoc, cPipeline, cHash, &out) != ok {
		return false, lastError()
	}
	return out != 0, nil
}

func cgoRepairSkillVariantChunks(entryDir, docID, pipeline, paramsHash, configJSON string) error {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cPipeline := cString(pipeline)
	defer freeCString(cPipeline)
	cHash := cString(paramsHash)
	defer freeCString(cHash)
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	if C.cyt_repair_skill_variant_chunks(cEntry, cDoc, cPipeline, cHash, cCfg) != ok {
		return lastError()
	}
	return nil
}

func cgoLoadSkillsIndexFromEntry(entryDir, docID, chunkDir string) (string, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	var cChunk *C.char
	if chunkDir != "" {
		cChunk = cString(chunkDir)
		defer freeCString(cChunk)
	}
	var out *C.char
	if C.cyt_load_skills_index_from_entry(cEntry, cDoc, cChunk, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoLoadMergedSkillDocumentJSON(entryDir, docID, chunkDir string) (string, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	var cChunk *C.char
	if chunkDir != "" {
		cChunk = cString(chunkDir)
		defer freeCString(cChunk)
	}
	var out *C.char
	if C.cyt_load_merged_skill_document_json(cEntry, cDoc, cChunk, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoFinalizeSkillDocumentJSON(entryDir, docID, contentSHA256, pipeline, indexParamsJSON, builtAt, sourcePath string) (string, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cHash := cString(contentSHA256)
	defer freeCString(cHash)
	cPipeline := cString(pipeline)
	defer freeCString(cPipeline)
	cParams := cString(indexParamsJSON)
	defer freeCString(cParams)
	cBuilt := cString(builtAt)
	defer freeCString(cBuilt)
	cPath := cString(sourcePath)
	defer freeCString(cPath)
	var out *C.char
	if C.cyt_finalize_skill_document_json(cEntry, cDoc, cHash, cPipeline, cParams, cBuilt, cPath, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoUpdateSkillDocumentSourcePath(entryDir, docID, sourcePath string) (string, error) {
	cEntry := cString(entryDir)
	defer freeCString(cEntry)
	cDoc := cString(docID)
	defer freeCString(cDoc)
	cPath := cString(sourcePath)
	defer freeCString(cPath)
	var out *C.char
	if C.cyt_update_skill_document_source_path(cEntry, cDoc, cPath, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoConfigureMemoryCache(configJSON string) error {
	cCfg := cString(configJSON)
	defer freeCString(cCfg)
	if C.cyt_configure_memory_cache(cCfg) != ok {
		return lastError()
	}
	return nil
}

func cgoConfigurePathConstants(mdExt, jsonExt, decomposedPrefix, decomposedRoot, catalogPrefix, defaultCatalogDir string, builderMemoryOnly, writeCatalogPrune bool) error {
	cMd := cString(mdExt)
	defer freeCString(cMd)
	cJSON := cString(jsonExt)
	defer freeCString(cJSON)
	cPrefix := cString(decomposedPrefix)
	defer freeCString(cPrefix)
	cRoot := cString(decomposedRoot)
	defer freeCString(cRoot)
	cCatalog := cString(catalogPrefix)
	defer freeCString(cCatalog)
	cDefault := cString(defaultCatalogDir)
	defer freeCString(cDefault)
	memOnly := C.int(0)
	if builderMemoryOnly {
		memOnly = 1
	}
	writePrune := C.int(0)
	if writeCatalogPrune {
		writePrune = 1
	}
	if C.cyt_configure_path_constants(cMd, cJSON, cPrefix, cRoot, cCatalog, cDefault, memOnly, writePrune) != ok {
		return lastError()
	}
	return nil
}

func cgoPathMdExt() (string, error) {
	var out *C.char
	if C.cyt_path_md_ext(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathJsonExt() (string, error) {
	var out *C.char
	if C.cyt_path_json_ext(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathDecomposedPrefix() (string, error) {
	var out *C.char
	if C.cyt_path_decomposed_prefix(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathDecomposedRoot() (string, error) {
	var out *C.char
	if C.cyt_path_decomposed_root(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathCatalogPrefix() (string, error) {
	var out *C.char
	if C.cyt_path_catalog_prefix(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathDefaultCatalogDir() (string, error) {
	var out *C.char
	if C.cyt_path_default_catalog_dir(&out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoPathBuilderMemoryOnly() (bool, error) {
	return fmtBoolQuery("PathBuilderMemoryOnly", C.cyt_path_builder_memory_only())
}

func cgoPathWriteCatalogPrune() (bool, error) {
	return fmtBoolQuery("PathWriteCatalogPrune", C.cyt_path_write_catalog_prune())
}

func cgoToolPassThrough(ctxJSON, toolID string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	cTool := cString(toolID)
	defer freeCString(cTool)
	return fmtBoolQuery("ToolPassThrough", C.cyt_tool_pass_through(cCtx, cTool))
}

func cgoFullPassThrough(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("FullPassThrough", C.cyt_full_pass_through(cCtx))
}

func cgoNeedsPartition(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("NeedsPartition", C.cyt_needs_partition(cCtx))
}

func cgoNeedsPrunedRecompose(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("NeedsPrunedRecompose", C.cyt_needs_pruned_recompose(cCtx))
}

func cgoSystemToolsPassThrough(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("SystemToolsPassThrough", C.cyt_system_tools_pass_through(cCtx))
}

func cgoMcpToolsPassThrough(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("McpToolsPassThrough", C.cyt_mcp_tools_pass_through(cCtx))
}

func cgoNeedsDescriptionReinstate(ctxJSON string) (bool, error) {
	cCtx := cString(ctxJSON)
	defer freeCString(cCtx)
	return fmtBoolQuery("NeedsDescriptionReinstate", C.cyt_needs_description_reinstate(cCtx))
}

func cgoIsSystemChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsSystemChunk", C.cyt_is_system_chunk(cItem))
}

func cgoIsNonSystemChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsNonSystemChunk", C.cyt_is_non_system_chunk(cItem))
}

func cgoIsDecomposedToolRootChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsDecomposedToolRootChunk", C.cyt_is_decomposed_tool_root_chunk(cItem))
}

func cgoIsDecomposedOptionalPropertyChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsDecomposedOptionalPropertyChunk", C.cyt_is_decomposed_optional_property_chunk(cItem))
}

func cgoIsSystemRootChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsSystemRootChunk", C.cyt_is_system_root_chunk(cItem))
}

func cgoIsMcpRootChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsMcpRootChunk", C.cyt_is_mcp_root_chunk(cItem))
}

func cgoIsSystemOptionalChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsSystemOptionalChunk", C.cyt_is_system_optional_chunk(cItem))
}

func cgoIsMcpOptionalChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsMcpOptionalChunk", C.cyt_is_mcp_optional_chunk(cItem))
}

func cgoIsDirectRootOptionalPropertyChunk(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("IsDirectRootOptionalPropertyChunk", C.cyt_is_direct_root_optional_property_chunk(cItem))
}

func cgoRootChunkPropertiesEmpty(itemJSON string) (bool, error) {
	cItem := cString(itemJSON)
	defer freeCString(cItem)
	return fmtBoolQuery("RootChunkPropertiesEmpty", C.cyt_root_chunk_properties_empty(cItem))
}

func cgoStashSystemTools(toolsJSON string) (string, error) {
	cInput := cString(toolsJSON)
	defer freeCString(cInput)
	var out *C.char
	if C.cyt_stash_system_tools(cInput, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRestoreSystemTools(stashJSON string) (string, error) {
	cInput := cString(stashJSON)
	defer freeCString(cInput)
	var out *C.char
	if C.cyt_restore_system_tools(cInput, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoStashMcpTools(toolsJSON string) (string, error) {
	cInput := cString(toolsJSON)
	defer freeCString(cInput)
	var out *C.char
	if C.cyt_stash_mcp_tools(cInput, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}

func cgoRestoreMcpTools(stashJSON string) (string, error) {
	cInput := cString(stashJSON)
	defer freeCString(cInput)
	var out *C.char
	if C.cyt_restore_mcp_tools(cInput, &out) != ok {
		return "", lastError()
	}
	return takeJSON(&out)
}
