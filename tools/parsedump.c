/* parsedump -- dump MicroPython's own parse tree, as an oracle for mpyc.parser.
 *
 * py/parse.c builds a tagged-pointer tree that nothing in MicroPython prints:
 * mp_parse_node_print is compiled out (MICROPY_DEBUG_PRINTERS is 0) and the
 * tree never reaches a .mpy.  But mp_parse() is exported and py/parse.h makes
 * the node layout public, so this calls the real parser and walks the result.
 *
 * It doubles as the check on our generated rule numbering: a struct node's
 * kind IS its rule index, so if these kinds line up with mpyc/grammar.py's
 * names, the table is right.
 *
 * Build with tools/build_parsedump.sh (reuses the pinned mpy-cross objects).
 *
 * Output, one line per node, tab separated, pre-order with explicit depth:
 *
 *     S <depth> <kind> <source_line> <num_children>    struct node
 *     L <depth> <what> <value>                         leaf
 *     E <message>                                      exception
 *     END
 *
 * where <what> is one of null/int/id/str/tok.  Kinds and token numbers are
 * printed numerically on purpose; names are applied on the Python side from
 * tables generated against this same build.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "py/builtin.h"
#include "py/compile.h"
#include "py/gc.h"
#include "py/lexer.h"
#include "py/mphal.h"
#include "py/nlr.h"
#include "py/obj.h"
#include "py/parse.h"
#include "py/runtime.h"
#include "py/stackctrl.h"

static long heap_size = 64 * 1024 * 1024;

/* Walk the tree iteratively: an input-shaped recursion could overflow the C
 * stack on deeply nested source, and we want this to survive what we feed it. */
typedef struct {
    mp_parse_node_t node;
    int depth;
} frame_t;

static frame_t *stack;
static size_t stack_len;
static size_t stack_alloc;

static void push(mp_parse_node_t node, int depth) {
    if (stack_len == stack_alloc) {
        stack_alloc = stack_alloc ? stack_alloc * 2 : 256;
        stack = realloc(stack, stack_alloc * sizeof(frame_t));
    }
    stack[stack_len].node = node;
    stack[stack_len].depth = depth;
    stack_len += 1;
}

static void dump(mp_parse_node_t root) {
    push(root, 0);
    while (stack_len > 0) {
        stack_len -= 1;
        mp_parse_node_t pn = stack[stack_len].node;
        int depth = stack[stack_len].depth;

        if (MP_PARSE_NODE_IS_NULL(pn)) {
            printf("L\t%d\tnull\t0\n", depth);
            continue;
        }
        if (MP_PARSE_NODE_IS_SMALL_INT(pn)) {
            printf("L\t%d\tint\t%ld\n", depth,
                (long)MP_PARSE_NODE_LEAF_SMALL_INT(pn));
            continue;
        }
        if (MP_PARSE_NODE_IS_LEAF(pn)) {
            const char *what = "?";
            switch (MP_PARSE_NODE_LEAF_KIND(pn)) {
                case MP_PARSE_NODE_ID:
                    what = "id";
                    break;
                case MP_PARSE_NODE_STRING:
                    what = "str";
                    break;
                case MP_PARSE_NODE_TOKEN:
                    what = "tok";
                    break;
                default:
                    break;
            }
            printf("L\t%d\t%s\t%lu\n", depth, what,
                (unsigned long)MP_PARSE_NODE_LEAF_ARG(pn));
            continue;
        }

        mp_parse_node_struct_t *pns = (mp_parse_node_struct_t *)pn;
        size_t n = MP_PARSE_NODE_STRUCT_NUM_NODES(pns);
        size_t kind = MP_PARSE_NODE_STRUCT_KIND(pns);
        printf("S\t%d\t%lu\t%lu\t%lu\n", depth, (unsigned long)kind,
            (unsigned long)pns->source_line, (unsigned long)n);
        /* Push in reverse so children print left to right. */
        for (size_t i = n; i > 0; --i) {
            push(pns->nodes[i - 1], depth + 1);
        }
    }
}

int main(int argc, char **argv) {
    const char *mode = "file";
    const char *path = NULL;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            mode = argv[++i];
        } else {
            path = argv[i];
        }
    }
    if (path == NULL) {
        fprintf(stderr, "usage: parsedump [--mode file|single|eval] FILE.py\n");
        return 2;
    }

    mp_parse_input_kind_t input_kind = MP_PARSE_FILE_INPUT;
    if (strcmp(mode, "single") == 0) {
        input_kind = MP_PARSE_SINGLE_INPUT;
    } else if (strcmp(mode, "eval") == 0) {
        input_kind = MP_PARSE_EVAL_INPUT;
    }

    mp_stack_ctrl_init();
    mp_stack_set_limit(80000 * (sizeof(void *) / 4));
    char *heap = malloc(heap_size);
    gc_init(heap, heap + heap_size);
    mp_init();

    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_lexer_t *lex = mp_lexer_new_from_file(qstr_from_str(path));
        mp_parse_tree_t tree = mp_parse(lex, input_kind);
        dump(tree.root);
        nlr_pop();
    } else {
        mp_obj_base_t *exc = (mp_obj_base_t *)nlr.ret_val;
        printf("E\t%s\n", qstr_str(exc->type->name));
        printf("END\n");
        return 0;
    }
    printf("END\n");
    return 0;
}

/* Supplied here because we replace mpy-cross's main(). */
static void stdout_print_strn(void *env, const char *str, size_t len) {
    (void)env;
    while (len > 0) {
        ssize_t n = write(STDOUT_FILENO, str, len);
        if (n <= 0) {
            break;
        }
        str += n;
        len -= (size_t)n;
    }
}

const mp_print_t mp_stdout_print = {NULL, stdout_print_strn};

/* mp_lexer_new_from_file needs this; the tree is read from one file only. */
mp_import_stat_t mp_import_stat(const char *path) {
    (void)path;
    return MP_IMPORT_STAT_NO_EXIST;
}

void nlr_jump_fail(void *val) {
    fprintf(stderr, "parsedump: FATAL uncaught NLR\n");
    exit(1);
}
