/* lexdump -- dump MicroPython's own token stream, as an oracle for mpyc.lexer.
 *
 * py/lexer.c is 1161 lines and MicroPython exposes no way to see its tokens,
 * so a faithful port has nothing to check itself against.  This links the
 * real lexer out of the pinned mpy-cross build and prints every token it
 * produces, which tests/test_lexer.py then diffs against ours.
 *
 * Build with tools/build_lexdump.sh (it reuses the mpy-cross objects and
 * config, so the token numbering matches that build exactly).
 *
 * Output is one line per token, tab separated, so it survives token text
 * containing newlines or quotes:
 *
 *     T <kind> <line> <column> <hex of vstr bytes>
 *     E <message>            on an exception
 *     END
 *
 * The token kind is printed as a number on purpose: the enum in py/lexer.h is
 * conditional on MICROPY_PY_FSTRINGS and MICROPY_PY_ASYNC_AWAIT, so numbers
 * from this build are the ground truth and name mapping happens on the Python
 * side against a table generated from the same header.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "py/compile.h"
#include "py/gc.h"
#include "py/lexer.h"
#include "py/mperrno.h"
#include "py/nlr.h"
#include "py/obj.h"
#include "py/runtime.h"
#include "py/stackctrl.h"
#include "py/mphal.h"
#include "py/builtin.h"

static long heap_size = 16 * 1024 * 1024;

static void print_hex(const char *data, size_t len) {
    for (size_t i = 0; i < len; ++i) {
        printf("%02x", (unsigned char)data[i]);
    }
}

static char *slurp(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        fprintf(stderr, "lexdump: cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(n > 0 ? n : 1);
    size_t got = fread(buf, 1, n, f);
    fclose(f);
    *out_len = got;
    return buf;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: lexdump FILE.py\n");
        return 2;
    }

    mp_stack_ctrl_init();
    mp_stack_set_limit(40000 * (sizeof(void *) / 4));
    char *heap = malloc(heap_size);
    gc_init(heap, heap + heap_size);
    mp_init();

    size_t len;
    char *src = slurp(argv[1], &len);

    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        /* free_len 0: the lexer must not take ownership of our buffer. */
        mp_lexer_t *lex = mp_lexer_new_from_str_len(
            qstr_from_str(argv[1]), src, len, 0);
        for (;;) {
            printf("T\t%d\t%u\t%u\t",
                (int)lex->tok_kind,
                (unsigned)lex->tok_line,
                (unsigned)lex->tok_column);
            print_hex(vstr_str(&lex->vstr), vstr_len(&lex->vstr));
            printf("\n");
            if (lex->tok_kind == MP_TOKEN_END) {
                break;
            }
            mp_lexer_to_next(lex);
        }
        mp_lexer_free(lex);
        nlr_pop();
    } else {
        /* An exception escaped the lexer: report its type and message. */
        mp_obj_base_t *exc = (mp_obj_base_t *)nlr.ret_val;
        printf("E\t%s\n", qstr_str(exc->type->name));
        mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
        printf("END\n");
        return 0;
    }
    printf("END\n");
    return 0;
}

/* mpy-cross's main.o defines these; we replace main(), so supply them here.
 * mp_stdout_print is referenced by the native emitters' debug output. */
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

/* The lexer never imports anything; a stub satisfies builtinimport.o. */
mp_import_stat_t mp_import_stat(const char *path) {
    (void)path;
    return MP_IMPORT_STAT_NO_EXIST;
}

void nlr_jump_fail(void *val) {
    fprintf(stderr, "lexdump: FATAL uncaught NLR\n");
    exit(1);
}

#ifndef NDEBUG
void MP_WEAK __assert_func(const char *file, int line, const char *func,
    const char *expr) {
    fprintf(stderr, "lexdump: assertion %s failed at %s:%d in %s\n",
        expr, file, line, func);
    exit(1);
}
#endif
