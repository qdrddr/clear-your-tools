#include <inttypes.h>
#include <stdio.h>

#include "examples/common.h"

int main(void) {
    char *index_out = NULL;
    const char *tools =
        "[{\"server\":\"srv\",\"tool\":\"search\",\"full_schema\":{"
        "\"inputSchema\":{\"type\":\"object\",\"properties\":{"
        "\"q\":{\"type\":\"string\"}},\"required\":[\"q\"]}}}]";
    const char *enums = "[]";

    if (!cyt_example_ok(cyt_build_catalog_index(tools, enums, &index_out),
                        "cyt_build_catalog_index")) {
        return 1;
    }
    char *catalog_index_json = cyt_example_take(&index_out);

    CYT_CytDecomposedCatalog *catalog = NULL;
    if (!cyt_example_ok(cyt_decomposed_catalog_from_catalog_index(
                            catalog_index_json, &catalog),
                        "cyt_decomposed_catalog_from_catalog_index")) {
        cyt_example_free(catalog_index_json);
        return 1;
    }

    char *dict_out = NULL;
    if (!cyt_example_ok(cyt_catalog_index_to_catalog_dict(
                            catalog_index_json, "src/catalog", &dict_out),
                        "cyt_catalog_index_to_catalog_dict")) {
        cyt_decomposed_catalog_free(catalog);
        cyt_example_free(catalog_index_json);
        return 1;
    }
    char *data_json = cyt_example_take(&dict_out);

    int64_t tool_count = cyt_retrieve_catalog_tool_count(data_json);
    if (tool_count < 0) {
        fprintf(stderr, "cyt_retrieve_catalog_tool_count failed\n");
        cyt_decomposed_catalog_free(catalog);
        cyt_example_free(catalog_index_json);
        cyt_example_free(data_json);
        return 1;
    }

    char *retrieve_out = NULL;
    if (!cyt_example_ok(cyt_retrieve_tools(data_json, catalog,
                                           catalog_index_json, 0, "[]", "{}",
                                           &retrieve_out),
                        "cyt_retrieve_tools")) {
        cyt_decomposed_catalog_free(catalog);
        cyt_example_free(catalog_index_json);
        cyt_example_free(data_json);
        return 1;
    }
    char *result = cyt_example_take(&retrieve_out);
    if (result == NULL || result[0] != '[') {
        fprintf(stderr, "retrieve result is not a JSON array\n");
        cyt_example_free(result);
        cyt_decomposed_catalog_free(catalog);
        cyt_example_free(catalog_index_json);
        cyt_example_free(data_json);
        return 1;
    }

    cyt_example_free(result);
    cyt_decomposed_catalog_free(catalog);
    cyt_example_free(catalog_index_json);
    cyt_example_free(data_json);

    printf("retrieve: decomposed catalog + retrieve_tools ok\n");
    return 0;
}
