/**
 * @file cyt_indexer.h
 * @brief cyt-indexer C FFI interface
 *
 * Tool schema decomposition and catalog indexing for MCP tool gating.
 *
 * # Memory Management
 *
 * - Strings returned via `char**` out parameters MUST be freed with `cyt_free_string()`.
 * - Opaque handles (`CytCatalogBuilder`, `CytDecomposedCatalog`, `CytSkillsBuilder`)
 *   MUST be freed with their matching `cyt_*_free()` function.
 * - Input C strings remain owned by the caller.
 *
 * # Thread Safety
 *
 * Error messages are stored in thread-local storage. Call `cyt_get_last_error()`
 * from the same thread that received a non-zero error code.
 *
 * # Return Conventions
 *
 * - `CYT_OK` (0) on success for status functions.
 * - Negative error codes on failure; see `cyt_get_last_error()`.
 * - JSON outputs: int return code + `char**` out param.
 * - Boolean queries: 1 true, 0 false, negative on error (or `int*` out with `CYT_OK`).
 */


#ifndef CYT_INDEXER_H
#define CYT_INDEXER_H

#pragma once

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
namespace cyt {
#endif  // __cplusplus

/*
 Reserved node id for YAML frontmatter (`0.md`).
 */
#define CYT_NODE_ID_FRONTMATTER 0

/*
 Reserved node id for preamble body text (`1.md`).
 */
#define CYT_NODE_ID_PREAMBLE 1

/*
 First node id assigned to markdown heading sections.
 */
#define CYT_CONTENT_NODE_ID_START 2

/*
 Success return code.
 */
#define CYT_CYT_OK 0

/*
 Null pointer argument error.
 */
#define CYT_CYT_ERR_NULL_PTR -1

/*
 Invalid UTF-8 encoding error.
 */
#define CYT_CYT_ERR_INVALID_UTF8 -2

/*
 JSON parse or serialization error.
 */
#define CYT_CYT_ERR_JSON -3

/*
 Memory allocation error.
 */
#define CYT_CYT_ERR_ALLOC -4

/*
 I/O or filesystem error.
 */
#define CYT_CYT_ERR_IO -5

/*
 Invalid opaque handle.
 */
#define CYT_CYT_ERR_INVALID_HANDLE -6

/*
 Internal panic (caught at FFI boundary).
 */
#define CYT_CYT_ERR_PANIC -7

/*
 Invalid argument / value error.
 */
#define CYT_CYT_ERR_INVALID_ARG -8

/*
 Opaque catalog builder handle.
 */
typedef struct CYT_CytCatalogBuilder CYT_CytCatalogBuilder;

/*
 Opaque in-memory decomposed catalog handle.
 */
typedef struct CYT_CytDecomposedCatalog CYT_CytDecomposedCatalog;

/*
 Opaque skills builder handle.
 */
typedef struct CYT_CytSkillsBuilder CYT_CytSkillsBuilder;

#ifdef __cplusplus
extern "C" {
#endif // __cplusplus

int cyt_full_pass_through(const char *ctx_json);

int cyt_needs_description_reinstate(const char *ctx_json);

int cyt_needs_partition(const char *ctx_json);

int cyt_needs_pruned_recompose(const char *ctx_json);

int cyt_system_tools_pass_through(const char *ctx_json);

int cyt_mcp_tools_pass_through(const char *ctx_json);

int cyt_tool_pass_through(const char *ctx_json, const char *tool_id);

int cyt_is_decomposed_tool_root_chunk(const char *item_json);

int cyt_is_decomposed_optional_property_chunk(const char *item_json);

int cyt_is_system_chunk(const char *item_json);

int cyt_is_non_system_chunk(const char *item_json);

int cyt_is_system_root_chunk(const char *item_json);

int cyt_is_mcp_root_chunk(const char *item_json);

int cyt_is_system_optional_chunk(const char *item_json);

int cyt_is_mcp_optional_chunk(const char *item_json);

int cyt_is_direct_root_optional_property_chunk(const char *item_json);

int cyt_root_chunk_properties_empty(const char *item_json);

int cyt_stash_system_tools(const char *input_json, char **out);

int cyt_restore_system_tools(const char *input_json, char **out);

int cyt_stash_mcp_tools(const char *input_json, char **out);

int cyt_restore_mcp_tools(const char *input_json, char **out);

int cyt_path_md_ext(char **out);

int cyt_path_json_ext(char **out);

int cyt_path_decomposed_prefix(char **out);

int cyt_path_decomposed_root(char **out);

int cyt_path_catalog_prefix(char **out);

int cyt_path_default_catalog_dir(char **out);

/*
 Return default BM25 cohesion config as JSON.
 */
int cyt_bm25_cohesion_default_config(char **out);

/*
 Chunk text with BM25 cohesion segmentation.
 */
int cyt_bm25_cohesion_chunk(const char *text, const char *config_json, char **out);

/*
 Override BM25 search defaults. `config_json` may be null or partial JSON.

 # Safety

 When non-null, `config_json` must be a valid null-terminated UTF-8 C string.
 */
int cyt_configure_bm25_defaults(const char *config_json);

/*
 Hash catalog documents plus analyzer settings.

 # Safety

 `data_json` and `out` must be valid pointers. `out` receives an allocated string
 that the caller must free with [`cyt_free_string`].
 */
int cyt_bm25_catalog_fingerprint(const char *data_json, char **out);

/*
 Score catalog json/md lists in-place and return the updated catalog JSON.

 # Safety

 `data_json`, `query`, and `out` must be valid pointers. `options_json` may be null.
 `out` receives an allocated JSON string that the caller must free with [`cyt_free_string`].
 */
int cyt_bm25_score_catalog(const char *data_json,
                           const char *query,
                           const char *options_json,
                           char **out);

/*
 Return excluded entry refs and trace metadata for frontmatter gating.

 # Safety

 `entries_json`, `query`, and `out` must be valid pointers. `out` receives an allocated
 JSON string that the caller must free with [`cyt_free_string`].
 */
int cyt_bm25_frontmatter_gate(const char *entries_json,
                              const char *query,
                              double upper_limit,
                              char **out);

/*
 Search skill chunks, reconstruct matches, return matches + trace JSON.

 # Safety

 `entries_json`, `query`, and `out` must be valid pointers. `excluded_json` may be null.
 `out` receives an allocated JSON string that the caller must free with [`cyt_free_string`].
 */
int cyt_bm25_search_skill_chunks(const char *entries_json,
                                 const char *query,
                                 double threshold,
                                 const char *excluded_json,
                                 char **out);

/*
 Reconstruct multiple skill doc groups in one native call.

 # Safety

 `groups_json` must be a valid null-terminated UTF-8 C string; `out` must be non-null.
 */
int cyt_batch_reconstruct_skill_matches(const char *groups_json, char **out);

/*
 Greedy budget selection over skill search survivors.

 # Safety

 `survivors_json` and `item_kind` must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_greedy_select_skill_items(const char *survivors_json,
                                  const char *item_kind,
                                  int64_t max_tokens,
                                  char **out);

/*
 Map a raw BM25 score to absolute similarity in `[0, 1]`.
 */
double cyt_exp_similarity(double raw);

/*
 Hash tool catalog content for cache keying.

 # Safety

 `tools_json` and `policy_fingerprint` must be valid null-terminated UTF-8 C strings.
 */
int cyt_tools_catalog_content_hash(const char *tools_json,
                                   const char *policy_fingerprint,
                                   char **out);

/*
 Ensure decomposed tool catalog from Anthropic tool dicts.

 # Safety

 All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_ensure_tool_catalog(const char *tools_json,
                            const char *policy_fingerprint,
                            const char *tools_root,
                            const char *policy,
                            char **out);

/*
 Ensure decomposed tool catalog from prepared entries and enums.

 # Safety

 All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_ensure_tool_catalog_from_entries(const char *entries_json,
                                         const char *enums_json,
                                         const char *policy_fingerprint,
                                         const char *tools_root,
                                         const char *policy,
                                         char **out);

/*
 Ensure page index (+ BM25 chunks when pipeline is bm25) for skill sources.

 # Safety

 All string pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_ensure_skills_registry(const char *source_paths_json,
                               const char *catalog_root,
                               const char *pageindex_config_json,
                               const char *pipeline,
                               const char *index_params_hash,
                               const char *policy,
                               char **out);

/*
 Apply in-memory cache tuning from a JSON object (`cache.memory` block).

 # Safety

 `config_json` must be a valid null-terminated UTF-8 C string when non-null.
 */
int cyt_configure_memory_cache(const char *config_json);

/*
 Count tools in a catalog dict JSON.

 # Safety

 `data_json` must be a valid null-terminated UTF-8 C string, or null (returns -1).
 */
long cyt_catalog_tool_count(const char *data_json);

/*
 Build a catalog index from tools and enums JSON arrays.

 # Safety

 `tools_json`, `enums_json`, and `out` must be valid pointers. `out` receives an
 allocated JSON string that the caller must free with [`cyt_free_string`].
 */
int cyt_build_catalog_index(const char *tools_json, const char *enums_json, char **out);

/*
 Convert Anthropic tools to catalog entries and enums.
 */
int cyt_anthropic_tools_to_catalog_entries(const char *tools_json, char **out);

/*
 Build catalog index from normalized tool entries.
 */
int cyt_build_catalog_from_tools(const char *tools_json, char **out);

/*
 Prepare a single tool catalog entry.
 */
int cyt_prepare_tool_entry(const char *server_name,
                           const char *name,
                           const char *description,
                           const char *input_schema_json,
                           char **out);

/*
 Convert one Anthropic tool to a catalog entry. Writes null to `out` when none.
 */
int cyt_anthropic_tool_to_catalog_entry(const char *tool_json, char **out);

/*
 Truncate a tool description to a token budget.
 */
int cyt_truncate_description(const char *description, unsigned long max_tokens, char **out);

/*
 Convert catalog index JSON to catalog dict for retrieval.
 */
int cyt_catalog_index_to_catalog_dict(const char *index_json,
                                      const char *catalog_prefix,
                                      char **out);

/*
 Return cached full/decomposed tool schema token metadata from catalog index JSON.
 */
int cyt_catalog_index_tool_schema_metadata(const char *index_json, char **out);

int cyt_catalog_builder_new(int memory_only,
                            const char *output_dir,
                            struct CYT_CytCatalogBuilder **out);

void cyt_catalog_builder_free(struct CYT_CytCatalogBuilder *builder);

int cyt_catalog_builder_add_tool(struct CYT_CytCatalogBuilder *builder, const char *entry_json);

int cyt_catalog_builder_get_tool_info(const struct CYT_CytCatalogBuilder *builder,
                                      const char *server_name,
                                      const char *tool_name,
                                      char **out);

int cyt_catalog_builder_build_index(struct CYT_CytCatalogBuilder *builder, char **out);

int cyt_catalog_builder_write_catalog(struct CYT_CytCatalogBuilder *builder, char **out);

int cyt_catalog_builder_to_catalog_dict(struct CYT_CytCatalogBuilder *builder,
                                        const char *catalog_prefix,
                                        char **out);

int cyt_write_catalog_index(const char *index_json, const char *output_dir, int prune);

int cyt_extract_document_text(const char *item_json, char **out);

int cyt_extract_level_info(const char *item_json, char **out);

int cyt_extract_json_catalog_document(const char *item_json, char **out);

int cyt_extract_md_catalog_document(const char *item_json, char **out);

/*
 Get the last error message for the current thread.

 Returns NULL if no error occurred. Valid until the next `cyt_*` call on this thread.

 # Safety

 No pointer arguments; safe to call from C when linked against this library.
 */
const char *cyt_get_last_error(void);

/*
 Clear the last error for the current thread.

 # Safety

 No pointer arguments; safe to call from C when linked against this library.
 */
void cyt_clear_error(void);

/*
 Free a string allocated by `cyt_*` functions. NULL is safe.

 # Safety

 `s` must be null or a pointer previously returned by a `cyt_*` out-parameter.
 */
void cyt_free_string(char *s);

/*
 Return the library version string (caller must free with `cyt_free_string`).

 # Safety

 `out` must be a valid pointer to a `char*` that receives an allocated string.
 */
int cyt_get_version(char **out);

int cyt_build_skills_index(const char *skill_dirs_json, const char *config_json, char **out);

int cyt_write_skills_index(const char *index_json, const char *output_dir);

int cyt_load_skills_index_from_dir(const char *catalog_dir, char **out);

int cyt_repair_skill_chunks(const char *entry_dir, const char *doc_id, const char *config_json);

int cyt_skills_index_from_decomposed_dir(const char *dir, char **out);

int cyt_md_to_tree(const char *markdown_content,
                   const char *source_path,
                   const char *config_json,
                   char **out);

int cyt_get_skill_document(const char *documents_json, const char *doc_id, char **out);

int cyt_get_skill_structure(const char *documents_json, const char *doc_id, char **out);

int cyt_get_skill_line_content_from_spec(const char *index_or_docs_json,
                                         const char *doc_id,
                                         const char *line_num_spec,
                                         char **out);

int cyt_get_skill_content_retrieve_result(const char *index_or_docs_json,
                                          const char *doc_id,
                                          const char *line_num_specs_json,
                                          const char *node_id_specs_json,
                                          const char *chunk_id_specs_json,
                                          const char *options_json,
                                          char **out);

int cyt_reconstruct_skill_markdown(const char *index_or_docs_json,
                                   const char *doc_id,
                                   const char *line_num_specs_json,
                                   const char *node_id_specs_json,
                                   const char *chunk_id_specs_json,
                                   const char *options_json,
                                   char **out);

int cyt_write_reconstructed_skill(const char *catalog_dir,
                                  const char *index_or_docs_json,
                                  const char *doc_id,
                                  const char *line_num_specs_json,
                                  const char *node_id_specs_json,
                                  const char *chunk_id_specs_json,
                                  const char *options_json,
                                  char **out);

int cyt_get_skill_line_content(const char *index_or_docs_json,
                               const char *doc_id,
                               const char *line_num_specs_json,
                               const char *node_id_specs_json,
                               const char *chunk_id_specs_json,
                               char **out);

int cyt_parse_skill_chunk_ids(const char *spec, char **out);

int cyt_parse_skill_node_ids(const char *spec, char **out);

/*
 Parse ``token_count`` from decomposed markdown/JSON frontmatter when present.
 */
int cyt_token_count_from_decomposed_frontmatter(const char *content, long *out);

int cyt_skills_builder_new(int memory_only,
                           const char *output_dir,
                           struct CYT_CytSkillsBuilder **out);

void cyt_skills_builder_free(struct CYT_CytSkillsBuilder *builder);

int cyt_skills_builder_build_from_dirs(struct CYT_CytSkillsBuilder *builder,
                                       const char *skill_dirs_json,
                                       const char *config_json,
                                       char **out);

int cyt_skills_builder_write_catalog(struct CYT_CytSkillsBuilder *builder, char **out);

int cyt_skills_builder_to_skills_index_json(const struct CYT_CytSkillsBuilder *builder, char **out);

int cyt_skills_builder_to_skills_dict(const struct CYT_CytSkillsBuilder *builder, char **out);

int cyt_reconstruct_options_default(char **out);

int cyt_build_page_index_only(const char *skill_dirs_json, const char *config_json, char **out);

int cyt_build_chunk_variant(const char *entry_dir,
                            const char *doc_id,
                            const char *pipeline,
                            const char *params_hash,
                            const char *config_json,
                            char **out);

int cyt_page_index_valid(const char *entry_dir, const char *content_sha256, int *out);

int cyt_chunk_variant_valid(const char *entry_dir,
                            const char *doc_id,
                            const char *pipeline,
                            const char *params_hash,
                            int *out);

int cyt_repair_skill_variant_chunks(const char *entry_dir,
                                    const char *doc_id,
                                    const char *pipeline,
                                    const char *params_hash,
                                    const char *config_json);

int cyt_load_skills_index_from_entry(const char *entry_dir,
                                     const char *doc_id,
                                     const char *chunk_dir,
                                     char **out);

int cyt_load_merged_skill_document_json(const char *entry_dir,
                                        const char *doc_id,
                                        const char *chunk_dir,
                                        char **out);

int cyt_finalize_skill_document_json(const char *entry_dir,
                                     const char *doc_id,
                                     const char *content_sha256,
                                     const char *pipeline,
                                     const char *index_params_json,
                                     const char *built_at,
                                     const char *source_path,
                                     char **out);

int cyt_update_skill_document_source_path(const char *entry_dir,
                                          const char *doc_id,
                                          const char *source_path,
                                          char **out);

int cyt_configure_path_constants(const char *md_ext,
                                 const char *json_ext,
                                 const char *decomposed_prefix,
                                 const char *decomposed_root,
                                 const char *catalog_prefix,
                                 const char *default_catalog_dir,
                                 int builder_memory_only,
                                 int write_catalog_prune);

int cyt_collect_enums(const char *schema_json, char **out);

int cyt_to_decomposed_key(const char *file_path, char **out);

int cyt_tool_id_from_decomposed_rel(const char *rel_path, char **out);

int cyt_get_root_tool_key(const char *file_path, char **out);

int cyt_path_builder_memory_only(void);

int cyt_path_write_catalog_prune(void);

/*
 Prune a tool catalog with BM25 scoring and retrieve upstream tools.

 # Safety

 All JSON pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_prune_catalog_bm25_and_retrieve(const char *catalog_json,
                                        const char *build_catalog_json,
                                        const char *catalog_index_json,
                                        const char *query,
                                        const char *scoring_ctx_json,
                                        const char *output_ctx_json,
                                        const char *options_json,
                                        char **out);

/*
 Classify optional catalog chunks and optionally count tool JSON tokens.

 # Safety

 `catalog_json` must be a valid null-terminated UTF-8 C string; `tools_json` may be null;
 `out` must be non-null.
 */
int cyt_classify_and_count_catalog(const char *catalog_json, const char *tools_json, char **out);

/*
 BM25 skill search with optional frontmatter gate and greedy budget selection.

 # Safety

 `entries_json`, `query`, and `options_json` must be valid null-terminated UTF-8 C strings
 (`options_json` may be null); `out` must be non-null.
 */
int cyt_search_skills_and_select(const char *entries_json,
                                 const char *query,
                                 const char *options_json,
                                 char **out);

/*
 Run skills BM25 search and tool BM25 prune in parallel.

 # Safety

 All JSON pointers must be valid null-terminated UTF-8 C strings; `out` must be non-null.
 */
int cyt_coordinate_bm25_prune(const char *skills_entries_json,
                              const char *catalog_json,
                              const char *build_catalog_json,
                              const char *catalog_index_json,
                              const char *query,
                              const char *scoring_ctx_json,
                              const char *output_ctx_json,
                              const char *options_json,
                              char **out);

/*
 Build rerankable node bodies from cached skill entries.

 # Safety

 `entries_json` must be a valid null-terminated UTF-8 C string; `out` must be non-null.
 */
int cyt_build_skill_node_catalog(const char *entries_json, char **out);

int cyt_tool_policies(char **out);

int cyt_policy_context_from_values(const char *config_json, char **out);

int cyt_effective_policy(const char *ctx_json, const char *tool_id, char **out);

int cyt_batch_tool_pass_through(const char *ctx_json, const char *tool_ids_json, char **out);

int cyt_partition_catalog(const char *data_json, const char *ctx_json, char **out);

int cyt_merge_catalog(const char *processed_json, const char *pinned_json, char **out);

int cyt_catalog_needs_partition(const char *data_json, const char *ctx_json);

int cyt_catalog_needs_pruned_recompose(const char *data_json, const char *ctx_json);

int cyt_request_pass_through(const char *ctx_json, const char *tools_json);

int cyt_filter_recompose_json_entries(const char *json_list_json,
                                      const char *ctx_json,
                                      double rerank_score,
                                      int use_default_rerank_score,
                                      const char *llm_selected_paths_json,
                                      char **out);

int cyt_mitigate_empty_optional_properties(const char *entries_json,
                                           const char *catalog_index_json,
                                           const char *ctx_json,
                                           const char *post_rerank_scored_json,
                                           const char *pipeline_json,
                                           char **out);

int cyt_append_description_reinstate_entries(const char *entries_json,
                                             const char *build_catalog_json,
                                             const char *catalog_index_json,
                                             const char *ctx_json,
                                             char **out);

int cyt_is_description_policy(const char *policy);

int cyt_scoring_policy(const char *policy, char **out);

int cyt_drop_recomposed_tools_with_empty_properties(const char *tools_json,
                                                    const char *catalog_index_json,
                                                    const char *ctx_json,
                                                    char **out);

int cyt_root_tool_id_from_chunk(const char *item_json, char **out);

int cyt_chunk_tool_id(const char *item_json, char **out);

int cyt_is_non_system_tool_id(const char *tool_id);

int cyt_is_system_tool_id(const char *tool_id);

int cyt_merge_tools_preserving_order(const char *original_json,
                                     const char *pruned_by_name_json,
                                     const char *stashed_by_name_json,
                                     char **out);

int cyt_split_anthropic_tools(const char *tools_json, char **out);

int cyt_entries_for_policy(const char *ctx_json, const char *all_entries_json, char **out);

int cyt_tools_for_catalog(const char *ctx_json, const char *tools_json, char **out);

int cyt_system_required_enum_values(const char *data_json, char **out);

int cyt_mcp_required_enum_values(const char *data_json, char **out);

int cyt_required_enum_values_by_tool(const char *data_json, char **out);

int cyt_optional_leaf_survived_rerank(const char *item_json,
                                      const char *ctx_json,
                                      double rerank_score,
                                      int use_default_rerank_score,
                                      const char *llm_selected_paths_json,
                                      int *out);

int cyt_anthropic_tool_is_system(const char *tool_json);

int cyt_anthropic_tool_is_mcp(const char *tool_json);

int cyt_direct_root_optional_chunks_for_tool(const char *items_json,
                                             const char *tool_id,
                                             char **out);

int cyt_tool_id_has_empty_decomposed_root(const char *catalog_index_json,
                                          const char *tool_id,
                                          int *out);

int cyt_tool_id_had_empty_original_root_properties(const char *catalog_index_json,
                                                   const char *tool_id,
                                                   int *out);

/*
 Classify optional chunks for many catalog items in one pass.

 Returns JSON `{"system":[bool,...],"mcp":[bool,...]}`.

 # Safety

 `items_json` must be a JSON array; `out` must be non-null.
 */
int cyt_classify_optional_chunks_batch(const char *items_json, char **out);

int cyt_decomposed_catalog_new(struct CYT_CytDecomposedCatalog **out);

void cyt_decomposed_catalog_free(struct CYT_CytDecomposedCatalog *catalog);

int cyt_decomposed_catalog_from_catalog_index(const char *index_json,
                                              struct CYT_CytDecomposedCatalog **out);

int cyt_decomposed_catalog_from_catalog_dict(const char *data_json,
                                             struct CYT_CytDecomposedCatalog **out);

int cyt_decomposed_catalog_has_json(const struct CYT_CytDecomposedCatalog *catalog,
                                    const char *key);

int cyt_decomposed_catalog_get_json(const struct CYT_CytDecomposedCatalog *catalog,
                                    const char *key,
                                    char **out);

int cyt_retrieve_core(const char *data_json,
                      const char *store_json,
                      const char *survivor_json,
                      int apply_decomposed_score_filter,
                      const char *policy_options_json,
                      char **out);

int cyt_load_catalog(const char *dir_path, char **out);

int cyt_chunk_survivor_key(const char *item_json, const char *section, char **out);

int cyt_removed_chunks(const char *full_catalog_json,
                       const char *surviving_json,
                       int apply_decomposed_score_filter,
                       char **out);

int cyt_retrieve_tools(const char *data_json,
                       struct CYT_CytDecomposedCatalog *catalog,
                       const char *catalog_index_json,
                       int apply_decomposed_score_filter,
                       const char *preserve_values_json,
                       const char *ctx_json,
                       char **out);

long cyt_retrieve_catalog_tool_count(const char *data_json);

int cyt_resolve_build_catalog(const char *catalog_json, const char *survivor_json, char **out);

int cyt_configure_runtime_defaults(double decomposed_score,
                                   double enum_score,
                                   double rerank_score,
                                   uintptr_t empty_optional_fallback_k,
                                   const char *default_system_policy,
                                   const char *default_mcp_policy);

double cyt_runtime_decomposed_score(void);

double cyt_runtime_enum_score(void);

double cyt_runtime_rerank_score(void);

uintptr_t cyt_runtime_empty_optional_fallback_k(void);

int cyt_runtime_default_system_policy(char **out);

int cyt_runtime_default_mcp_policy(char **out);

/*
 Count tokens in UTF-8 text using the configured tiktoken encoding.

 Returns the token count on success, or `-1` on error (`cyt_get_last_error()`).

 # Safety

 `text` must be a valid null-terminated UTF-8 C string, or null (returns -1).
 */
long cyt_count_tokens(const char *text);

/*
 Count tokens for compact JSON text.

 Returns the token count on success, or `-1` on error.

 # Safety

 `json` must be a valid null-terminated UTF-8 C string, or null (returns -1).
 */
long cyt_count_json_tokens(const char *json);

/*
 Override tokenizer defaults. `config_json` may be null or partial JSON:
 `{"encoding":"cl100k_base","allowed_special":"all"|"none"}`.

 # Safety

 When non-null, `config_json` must be a valid null-terminated UTF-8 C string.
 */
int cyt_configure_tokenizer_defaults(const char *config_json);

/*
 Count tokens for multiple UTF-8 strings.

 `texts_json` must be a JSON array of strings. Writes a JSON array of counts to `out`.

 # Safety

 When non-null, `texts_json` must be valid UTF-8 JSON; `out` must be non-null.
 */
int cyt_count_tokens_batch(const char *texts_json, char **out);

#ifdef __cplusplus
}  // extern "C"
#endif  // __cplusplus

#ifdef __cplusplus
}  // namespace cyt
#endif  // __cplusplus

#endif  /* CYT_INDEXER_H */
