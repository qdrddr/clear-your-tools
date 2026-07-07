#include <stdio.h>
#include <string.h>

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

    const char *tool_ids = "[\"Agent\",\"grep\"]";
    char *batch_out = NULL;
    const char *pass_ctx = "{\"system_policy\":\"always_include\",\"mcp_"
                           "policy\":\"always_include\"}";
    if (!cyt_example_ok(
            cyt_batch_tool_pass_through(pass_ctx, tool_ids, &batch_out),
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

    char *policy_out = NULL;
    const char *mcp_ctx = "{\"system_policy\":\"prune_optional\","
                          "\"mcp_policy\":\"prune_all\","
                          "\"tool_kind\":\"mcp\"}";
    if (!cyt_example_ok(
            cyt_effective_policy(mcp_ctx, "tools.demo.org.search", &policy_out),
            "cyt_effective_policy tool_kind")) {
        return 1;
    }
    char *policy = cyt_example_take(&policy_out);
    if (policy == NULL || strcmp(policy, "prune_all") != 0) {
        fprintf(stderr, "unexpected effective_policy with tool_kind=mcp: %s\n",
                policy ? policy : "(null)");
        cyt_example_free(policy);
        return 1;
    }
    cyt_example_free(policy);

    if (cyt_is_description_policy("prune_optional_descriptions") != 1) {
        fprintf(stderr, "cyt_is_description_policy failed\n");
        return 1;
    }

    printf("policies: partition/merge ok\n");
    return 0;
}
