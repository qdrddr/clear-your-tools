#include <stdio.h>

#include "examples/common.h"

int main(void) {
    const char *md = "# Title\n\nBody\n\n## Sub\n\nMore";
    char *tree_out = NULL;
    if (!cyt_example_ok(cyt_md_to_tree(md, "skill.md", "{}", &tree_out),
                        "cyt_md_to_tree")) {
        return 1;
    }
    char *tree = cyt_example_take(&tree_out);
    if (tree == NULL || strstr(tree, "Title") == NULL) {
        fprintf(stderr, "md_to_tree missing content\n");
        cyt_example_free(tree);
        return 1;
    }
    cyt_example_free(tree);

    char *chunk_ids_out = NULL;
    if (!cyt_example_ok(cyt_parse_skill_chunk_ids("8-10", &chunk_ids_out),
                        "cyt_parse_skill_chunk_ids")) {
        return 1;
    }
    char *chunk_ids = cyt_example_take(&chunk_ids_out);
    if (chunk_ids == NULL || strstr(chunk_ids, "8") == NULL) {
        fprintf(stderr, "unexpected chunk id list\n");
        cyt_example_free(chunk_ids);
        return 1;
    }
    cyt_example_free(chunk_ids);

    char *bm25_out = NULL;
    if (!cyt_example_ok(cyt_bm25_cohesion_default_config(&bm25_out),
                        "cyt_bm25_cohesion_default_config")) {
        return 1;
    }
    char *bm25_cfg = cyt_example_take(&bm25_out);

    char *chunks_out = NULL;
    const char *text = "Alpha one two three. Beta finance market stocks.";
    if (!cyt_example_ok(cyt_bm25_cohesion_chunk(text, bm25_cfg, &chunks_out),
                        "cyt_bm25_cohesion_chunk")) {
        cyt_example_free(bm25_cfg);
        return 1;
    }
    cyt_example_free(bm25_cfg);
    cyt_example_free(cyt_example_take(&chunks_out));

    printf("skills: pageindex + bm25 ok\n");
    return 0;
}
