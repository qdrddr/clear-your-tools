#include <stdio.h>

#include "examples/common.h"

int main(void) {
    char *policies_out = NULL;
    if (!cyt_example_ok(cyt_tool_policies(&policies_out),
                        "cyt_tool_policies")) {
        return 1;
    }
    char *policies = cyt_example_take(&policies_out);
    if (policies == NULL || strstr(policies, "prune_optional") == NULL) {
        fprintf(stderr, "unexpected tool policies JSON\n");
        cyt_example_free(policies);
        return 1;
    }
    cyt_example_free(policies);

    char *ctx_out = NULL;
    const char *ctx_values = "{\"system_policy\":\"prune_optional\","
                             "\"mcp_policy\":\"prune_all\"}";
    if (!cyt_example_ok(cyt_policy_context_from_values(ctx_values, &ctx_out),
                        "cyt_policy_context_from_values")) {
        return 1;
    }
    char *ctx = cyt_example_take(&ctx_out);

    const char *data =
        "{\"json\":[],\"md\":[],\"tools\":[{\"name\":\"Agent\"}]}";
    char *partition_out = NULL;
    if (!cyt_example_ok(cyt_partition_catalog(data, ctx, &partition_out),
                        "cyt_partition_catalog")) {
        cyt_example_free(ctx);
        return 1;
    }
    char *partitioned = cyt_example_take(&partition_out);

    char *merged_out = NULL;
    if (!cyt_example_ok(cyt_merge_catalog(partitioned, "{}", &merged_out),
                        "cyt_merge_catalog")) {
        cyt_example_free(partitioned);
        cyt_example_free(ctx);
        return 1;
    }
    cyt_example_free(cyt_example_take(&merged_out));
    cyt_example_free(partitioned);
    cyt_example_free(ctx);

    if (cyt_is_description_policy("prune_optional_descriptions") != 1) {
        fprintf(stderr, "cyt_is_description_policy failed\n");
        return 1;
    }

    printf("policies: partition/merge ok\n");
    return 0;
}
