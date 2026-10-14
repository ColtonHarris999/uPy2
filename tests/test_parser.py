"""Differential test: mpyc.parser against MicroPython's own parser.

The oracle is tools/parsedump.c, which links the real py/parse.c out of the
pinned mpy-cross build and walks the tree it produces.  This parses the same
files and compares the trees node by node: kind, source line, child count,
and every leaf's kind and value.

Identifier and string leaves are compared by *text*, since dynamic qstr
numbers depend on how many the build already held.  Folded constants are
compared by repr.

    python3 tests/test_parser.py --parsedump build/parsedump PATH...

Build the oracle first with tools/build_parsedump.sh.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mpyc import grammar as G  # noqa: E402
from mpyc import parser as P  # noqa: E402
from mpyc import parsetree as PT  # noqa: E402
from mpyc import tokens as T  # noqa: E402
from mpyc.lexer import NotImplementedFeature  # noqa: E402

MODES = {
    "file": P.PARSE_FILE_INPUT,
    "single": P.PARSE_SINGLE_INPUT,
    "eval": P.PARSE_EVAL_INPUT,
}


def oracle_lines(parsedump, path, mode):
    """Flatten the oracle's dump to comparable tuples, or (None, error)."""
    p = subprocess.run([parsedump, "--mode", mode, path], capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    rows = []
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "S":
            rows.append(("S", int(f[1]), int(f[2]), int(f[3]), int(f[4])))
        elif f[0] == "C":
            rows.append(("C", int(f[1]), f[3]))
        elif f[0] == "L":
            if f[2] in ("id", "str"):
                rows.append(("L", int(f[1]), f[2], bytes.fromhex(f[3])))
            else:
                rows.append(("L", int(f[1]), f[2], int(f[3])))
        elif f[0] == "E":
            return None, f[1]
        elif f[0] == "END":
            break
    if p.returncode != 0:
        return None, "exit %d" % p.returncode
    return rows, None


def our_lines(tree, pool):
    """The same flattening of our tree: pre-order with explicit depth."""
    rows = []
    stack = [(tree.root, 0)]
    while stack:
        node, depth = stack.pop()
        if PT.is_null(node):
            rows.append(("L", depth, "null", 0))
            continue
        if PT.is_small_int(node):
            rows.append(("L", depth, "int", PT.small_int_value(node)))
            continue
        if PT.is_leaf(node):
            if PT.is_id(node):
                rows.append(("L", depth, "id", pool.text(PT.leaf_arg(node))))
            elif PT.is_string(node):
                rows.append(("L", depth, "str", pool.text(PT.leaf_arg(node))))
            elif PT.is_token(node):
                rows.append(("L", depth, "tok", PT.leaf_arg(node)))
            else:
                rows.append(("L", depth, "?", PT.leaf_arg(node)))
            continue
        kind = tree.struct_kind(node)
        if kind == G.RULE_const_object:
            value = tree.const(tree.child(node, 0))
            rows.append(("C", depth, const_form(value)))
            continue
        n = tree.num_children(node)
        rows.append(("S", depth, kind, tree.source_line(node), n))
        for i in range(n - 1, -1, -1):
            stack.append((tree.child(node, i), depth + 1))
    return rows


def const_form(value):
    """Our constant in the oracle's structured spelling.

    Unambiguous on purpose: text as raw hex, floats as %.17g, so neither
    float printing nor string decoding can make equal values look different.
    """
    t = type(value)
    if value is None:
        return "none"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if t is PT.MpStr:
        return "str:" + value.data.hex()
    if t is bytes:
        return "bytes:" + value.hex()
    if t is int:
        return "int:%d" % value
    if t is float:
        # Python's %g drops a NaN's sign, where glibc's prints "-nan"; read
        # the sign bit directly so the two agree.
        if value != value:
            import math
            return "float:%snan" % ("-" if math.copysign(1.0, value) < 0 else "")
        return "float:%.17g" % value
    if t is tuple:
        return "tuple(%d%s)" % (len(value),
                                "".join("," + const_form(v) for v in value))
    if value is Ellipsis:
        return "other:Ellipsis"
    return "other:%r" % (value,)


def describe(row):
    if row[0] == "S":
        return "S d=%d %s line=%d n=%d" % (row[1], G.rule_name(row[2]),
                                           row[3], row[4])
    if row[0] == "C":
        return "C d=%d CONST %s" % (row[1], row[2])
    if row[2] == "tok":
        return "L d=%d tok %s" % (row[1], T.token_name(row[3]))
    return "L d=%d %s %r" % (row[1], row[2], row[3])


def compare(parsedump, path, mode):
    ref, err = oracle_lines(parsedump, path, mode)
    try:
        with open(path, "rb") as f:
            src = f.read()
        lex_pool = P.QstrPool()
        from mpyc.lexer import Lexer
        pr = P.Parser(Lexer(path, src), MODES[mode], lex_pool)
        tree = pr.parse()
        mine = our_lines(tree, pr.pool)
    except NotImplementedFeature as e:
        return "not-implemented", str(e)
    except P.ParseError as e:
        if ref is None:
            return "both-error", "ours: %s / oracle: %s" % (e, err)
        return "we-error-only", "%s (oracle produced %d nodes)" % (e, len(ref))
    except RecursionError as e:
        return "recursion", repr(e)
    except Exception as e:  # noqa: BLE001
        if ref is None:
            return "both-error", "ours: %r / oracle: %s" % (e, err)
        return "error", repr(e)
    if ref is None:
        return "oracle-error-only", "oracle: %s, we produced %d nodes" % (
            err, len(mine))
    if mine == ref:
        return "ok", None
    n = min(len(mine), len(ref))
    for i in range(n):
        if mine[i] != ref[i]:
            ctx = "\n    after  : %s" % describe(ref[i - 1]) if i else ""
            return "mismatch", ("node %d of %d%s\n    oracle : %s\n    mine   : %s"
                                % (i, len(ref), ctx, describe(ref[i]),
                                   describe(mine[i])))
    longer = mine if len(mine) > n else ref
    return "mismatch", ("length %d vs oracle %d; first extra: %s"
                        % (len(mine), len(ref), describe(longer[n])))


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
    parsedump = None
    paths = []
    mode = "file"
    show = 6
    verbose = False
    it = iter(argv)
    for a in it:
        if a == "--parsedump":
            parsedump = next(it)
        elif a == "--mode":
            mode = next(it)
        elif a == "--show":
            show = int(next(it))
        elif a == "-v":
            verbose = True
        else:
            paths.append(a)
    if parsedump is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parsedump = os.path.join(here, "build", "parsedump")
    if not os.path.exists(parsedump):
        print("ERROR: no oracle at %s -- run tools/build_parsedump.sh"
              % parsedump)
        return 2

    counts = {}
    shown = 0
    for path in iter_files(paths):
        status, detail = compare(parsedump, path, mode)
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
