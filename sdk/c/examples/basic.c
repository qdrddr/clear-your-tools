#include <inttypes.h>
#include <stdio.h>

#include "examples/common.h"

int main(void) {
    char *out = NULL;
    const char *tools = "[]";
    const char *enums = "[]";

    if (!cyt_example_ok(cyt_build_catalog_index(tools, enums, &out),
                        "cyt_build_catalog_index")) {
        return 1;
    }

    char *json = cyt_example_take(&out);
    if (json == NULL || strstr(json, "\"tools\"") == NULL) {
        fprintf(stderr, "unexpected catalog index JSON\n");
        cyt_example_free(json);
        return 1;
    }
    cyt_example_free(json);

    const char *data = "{\"json\":[],\"md\":[]}";
    int64_t count = cyt_catalog_tool_count(data);
    if (count != 0) {
        fprintf(stderr, "expected 0 tools, got %" PRId64 "\n", count);
        return 1;
    }

    printf("basic: build_catalog_index ok\n");
    return 0;
}
