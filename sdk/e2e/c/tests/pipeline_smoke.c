#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cyt_indexer.h"

static int expect_ok(int code, const char *fn) {
    if (code == CYT_CYT_OK) {
        return 1;
    }
    const char *err = cyt_get_last_error();
    fprintf(stderr, "%s failed (%d): %s\n", fn, code, err ? err : "(no message)");
    return 0;
}

static char *read_fixture(const char *name) {
    const char *candidates[] = {
        "../../fixtures/",
        "../fixtures/",
        "../../../sdk/e2e/fixtures/",
        NULL,
    };
    for (int i = 0; candidates[i] != NULL; i++) {
        char path[512];
        snprintf(path, sizeof(path), "%s%s", candidates[i], name);
        FILE *f = fopen(path, "r");
        if (f == NULL) {
            continue;
        }
        fseek(f, 0, SEEK_END);
        long len = ftell(f);
        fseek(f, 0, SEEK_SET);
        if (len <= 0) {
            fclose(f);
            return NULL;
        }
        char *buf = malloc((size_t)len + 1);
        if (buf == NULL) {
            fclose(f);
            return NULL;
        }
        if (fread(buf, 1, (size_t)len, f) != (size_t)len) {
            free(buf);
            fclose(f);
            return NULL;
        }
        buf[len] = '\0';
        fclose(f);
        return buf;
    }
    return NULL;
}

int main(void) {
    char *catalog = read_fixture("bm25_catalog.json");
    if (catalog == NULL) {
        catalog = strdup(
            "{\"json\":[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\","
            "\"content\":\"Read files from disk path storage\"}],\"md\":[]}");
    }

    char *classify_out = NULL;
    if (!expect_ok(cyt_classify_and_count_catalog(catalog, NULL, &classify_out),
                   "cyt_classify_and_count_catalog")) {
        free(catalog);
        return 1;
    }
    if (classify_out == NULL || strstr(classify_out, "optional_chunk_count") == NULL) {
        fprintf(stderr, "unexpected classify_and_count_catalog JSON\n");
        cyt_free_string(classify_out);
        free(catalog);
        return 1;
    }
    cyt_free_string(classify_out);

    const char *ctx =
        "{\"system_policy\":\"always_include\",\"mcp_policy\":\"always_include\"}";
    const char *tool_ids = "[\"Agent\",\"grep\"]";
    char *batch_out = NULL;
    if (!expect_ok(cyt_batch_tool_pass_through(ctx, tool_ids, &batch_out),
                   "cyt_batch_tool_pass_through")) {
        free(catalog);
        return 1;
    }
    if (batch_out == NULL || strstr(batch_out, "true") == NULL) {
        fprintf(stderr, "unexpected batch_tool_pass_through JSON\n");
        cyt_free_string(batch_out);
        free(catalog);
        return 1;
    }
    cyt_free_string(batch_out);

    char *optional_out = NULL;
    const char *items = "[{\"file_path\":\"schemas/decomposed/mcp__test__read.json\"}]";
    if (!expect_ok(cyt_classify_optional_chunks_batch(items, &optional_out),
                   "cyt_classify_optional_chunks_batch")) {
        free(catalog);
        return 1;
    }
    if (optional_out == NULL || strstr(optional_out, "\"system\"") == NULL) {
        fprintf(stderr, "unexpected classify_optional_chunks_batch JSON\n");
        cyt_free_string(optional_out);
        free(catalog);
        return 1;
    }
    cyt_free_string(optional_out);

    free(catalog);
    return 0;
}
