#ifndef CYT_EXAMPLE_COMMON_H
#define CYT_EXAMPLE_COMMON_H

#include "include/cyt_indexer.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int cyt_example_ok(int code, const char *fn) {
    if (code == CYT_CYT_OK) {
        return 1;
    }
    const char *err = cyt_get_last_error();
    fprintf(stderr, "%s failed (%d): %s\n", fn, code,
            err ? err : "(no message)");
    return 0;
}

static char *cyt_example_take(char **out) {
    char *s = *out;
    *out = NULL;
    return s;
}

static void cyt_example_free(char *s) {
    if (s != NULL) {
        cyt_free_string(s);
    }
}

#endif /* CYT_EXAMPLE_COMMON_H */
