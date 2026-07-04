#include <stdio.h>
#include <string.h>

#include "examples/common.h"

int main(void) {
    const char *catalog =
        "{\"json\":[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\","
        "\"content\":\"Read files from disk path storage\"},"
        "{\"file_path\":\"schemas/decomposed/mcp__test__write.json\","
        "\"content\":\"Write output to unrelated finance topic\"}],\"md\":[]}";
    const char *ctx_values = "{\"system_policy\":\"always_include\",\"mcp_"
                             "policy\":\"always_include\"}";

    char *classify_out = NULL;
    if (!cyt_example_ok(
            cyt_classify_and_count_catalog(catalog, NULL, &classify_out),
            "cyt_classify_and_count_catalog")) {
        return 1;
    }
    char *classified = cyt_example_take(&classify_out);
    if (classified == NULL ||
        strstr(classified, "optional_chunk_count") == NULL) {
        fprintf(stderr, "unexpected classify_and_count_catalog JSON\n");
        cyt_example_free(classified);
        return 1;
    }
    cyt_example_free(classified);

    const char *tool_ids = "[\"Agent\",\"grep\"]";
    char *batch_out = NULL;
    if (!cyt_example_ok(
            cyt_batch_tool_pass_through(ctx_values, tool_ids, &batch_out),
            "cyt_batch_tool_pass_through")) {
        return 1;
    }
    char *batch_flags = cyt_example_take(&batch_out);
    if (batch_flags == NULL || strstr(batch_flags, "true") == NULL) {
        fprintf(stderr, "unexpected batch_tool_pass_through JSON\n");
        cyt_example_free(batch_flags);
        return 1;
    }
    cyt_example_free(batch_flags);

    char *optional_out = NULL;
    const char *items =
        "[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\"}]";
    if (!cyt_example_ok(
            cyt_classify_optional_chunks_batch(items, &optional_out),
            "cyt_classify_optional_chunks_batch")) {
        return 1;
    }
    char *optional = cyt_example_take(&optional_out);
    if (optional == NULL || strstr(optional, "\"system\"") == NULL) {
        fprintf(stderr, "unexpected classify_optional_chunks_batch JSON\n");
        cyt_example_free(optional);
        return 1;
    }
    cyt_example_free(optional);

    printf("pipeline: classify/batch ok\n");
    return 0;
}
