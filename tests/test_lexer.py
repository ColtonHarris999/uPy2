"""Differential test: mpyc.lexer against MicroPython's own lexer.

The oracle is tools/lexdump.c, which links the real py/lexer.c out of the
pinned mpy-cross build and prints every token it produces.  This runs both
over the same files and compares kind, line, column and text.

    python3 tests/test_lexer.py --lexdump build/lexdump PATH...

Build the oracle first with tools/build_lexdump.sh.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mpyc import lexer as L  # noqa: E402
from mpyc import tokens as T  # noqa: E402


def oracle_tokens(lexdump, path):
    """[(kind, line, column, text_bytes)] from the C lexer, or ('E', msg)."""
    p = subprocess.run([lexdump, path], capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    toks = []
    for line in out.splitlines():
        if line.startswith("T\t"):
            _, kind, ln, col, hexed = line.split("\t", 4)
            toks.append((int(kind), int(ln), int(col), bytes.fromhex(hexed)))
        elif line.startswith("E\t"):
            return None, line.split("\t", 1)[1]
        elif line == "END":
            break
    return toks, None


def our_tokens(path, limit=200000):
    with open(path, "rb") as f:
        src = f.read()
    lex = L.Lexer(path, src)
    toks = []
    while True:
        toks.append((lex.tok_kind, lex.tok_line, lex.tok_column, bytes(lex.vstr)))
        if lex.tok_kind == T.MP_TOKEN_END:
            break
        if len(toks) > limit:
            raise RuntimeError("runaway lexer (over %d tokens)" % limit)
        lex.to_next()
    return toks


def describe(tok):
    return "%s@%d:%d %r" % (T.token_name(tok[0]), tok[1], tok[2], tok[3])


def compare(lexdump, path, verbose=False):
    ref, err = oracle_tokens(lexdump, path)
    try:
        mine = our_tokens(path)
    except L.NotImplementedFeature as e:
        return "not-implemented", str(e)
    except Exception as e:  # noqa: BLE001
        if ref is None:
            # Both sides refused the input.
            return "both-error", "%s / oracle: %s" % (repr(e), err)
        return "error", repr(e)
    if ref is None:
        return "oracle-error-only", "oracle raised %s, we produced %d tokens" % (
            err, len(mine))
    if mine == ref:
        return "ok", None
    # Report the first divergence.
    n = min(len(mine), len(ref))
    for i in range(n):
        if mine[i] != ref[i]:
            ctx = ""
            if i:
                ctx = "\n    after: %s" % describe(ref[i - 1])
            return "mismatch", ("token %d of %d%s\n    oracle: %s\n    mine  : %s"
                                % (i, len(ref), ctx, describe(ref[i]),
                                   describe(mine[i])))
    return "mismatch", ("length %d vs oracle %d; first extra: %s"
                        % (len(mine), len(ref),
                           describe((mine if len(mine) > n else ref)[n])))


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, fs in os.walk(p):
                for f in sorted(fs):
                    if f.endswith(".py"):
                        yield os.path.join(root, f)
        elif p.endswith(".py"):
            yield p


def main(argv):
    lexdump = None
    paths = []
    verbose = False
    show = 6
    it = iter(argv)
    for a in it:
        if a == "--lexdump":
            lexdump = next(it)
        elif a == "-v":
            verbose = True
        elif a == "--show":
            show = int(next(it))
        else:
            paths.append(a)
    if lexdump is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lexdump = os.path.join(here, "build", "lexdump")
    if not os.path.exists(lexdump):
        print("ERROR: no oracle at %s -- run tools/build_lexdump.sh" % lexdump)
        return 2

    counts = {}
    shown = 0
    for path in iter_files(paths):
        status, detail = compare(lexdump, path, verbose)
        counts[status] = counts.get(status, 0) + 1
        if status != "ok" and shown < show:
            shown += 1
            print("%-18s %s" % (status, path))
            if detail:
                for line in detail.splitlines():
                    print("    %s" % line)
            sys.stdout.flush()
        elif status == "ok" and verbose:
            print("ok                 %s" % path)
    print("summary: " + " ".join("%s=%d" % kv for kv in sorted(counts.items())))
    bad = sum(v for k, v in counts.items()
              if k not in ("ok", "both-error", "not-implemented"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
