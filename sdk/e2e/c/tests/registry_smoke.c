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

int main(void) {
    char *out = NULL;
    const char *tools =
        "[{"
        "\"id\":\"mcp__test__foo\","
        "\"server\":\"test\","
        "\"tool\":\"mcp__test__foo\","
        "\"summary\":\"A test tool\","
        "\"full_schema\":{"
        "\"id\":\"mcp__test__foo\","
        "\"name\":\"mcp__test__foo\","
        "\"description\":\"A test tool\","
        "\"inputSchema\":{"
        "\"type\":\"object\","
        "\"properties\":{"
        "\"required_field\":{\"type\":\"string\"},"
        "\"optional_field\":{\"type\":\"string\",\"description\":\"opt\"}"
        "},"
        "\"required\":[\"required_field\"]"
        "}"
        "}"
        "}]";

    if (!expect_ok(cyt_build_catalog_index(tools, "[]", &out), "cyt_build_catalog_index")) {
        return 1;
    }
    if (out == NULL || strstr(out, "schemas/decomposed/mcp__test__foo.json") == NULL) {
        fprintf(stderr, "expected decomposed path in catalog index JSON\n");
        cyt_free_string(out);
        return 1;
    }
    cyt_free_string(out);

    const char *empty_catalog = "{\"json\":[],\"md\":[]}";
    if (cyt_catalog_tool_count(empty_catalog) != 0) {
        fprintf(stderr, "expected 0 tools in empty catalog\n");
        return 1;
    }

    return 0;
}
