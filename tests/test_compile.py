"""Differential test for the whole compiler, against mpy-cross.

Every earlier stage was checked against a piece of the oracle -- the token
stream, the parse tree, a prelude re-encoded.  This one checks the thing that
actually matters: compile a file and require the ``.mpy`` to be byte-identical
to what ``mpy-cross`` produces for it.  That exercises the lexer, the parser,
the constant folder, the scope analysis, the emitter, the qstr and constant
tables and the container format, all at once, and a single wrong byte
anywhere fails.

The parser is handed *mpy-cross's* qstr pool rather than the device's.  The
shipped default is the device's, because a string literal's fate (a qstr leaf
or a constant object) depends on the pool the code will run against; for
comparing against mpy-cross we must use its pool or every long literal that
only one build has interned would read as a bug.  See ``mpyc/parser.py``.

Files the oracle itself rejects are checked the other way round: we must
reject them too, and never accept something it refused.

    python3 tests/test_compile.py --mpy-cross PATH CORPUS...
"""
import os
import subprocess
import sys
import tempfile
import traceback

sys.path.insert(0, ".")
sys.path.insert(0, "..")
sys.path.insert(0, "tests")

from mpyc import compile as C  # noqa: E402
from mpyc import mpy_format as MPY  # noqa: E402
from mpyc import parser as P  # noqa: E402
from mpyc import qstrtable  # noqa: E402
from mpyc.lexer import Lexer, NotImplementedFeature  # noqa: E402

_fails = []
_ORACLE_POOL = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def oracle_preloaded():
    if not _ORACLE_POOL:
        try:
            from oracle_qstr_pool import POOL
        except ImportError:
            print("warning: tests/oracle_qstr_pool.py is missing; long "
                  "string literals will be classified against an empty pool")
            POOL = frozenset()
        _ORACLE_POOL.append(POOL)
    return _ORACLE_POOL[0]


def compile_ours(name, src):
    """Compile *src* the way mpy-cross would, as ``.mpy`` bytes."""
    pr = P.Parser(Lexer(name, src), P.PARSE_FILE_INPUT,
                  P.QstrPool(oracle_preloaded()))
    tree = pr.parse()
    return MPY.encode(C.compile_to_mpy(tree, name.encode("utf-8")))


def only_float_spelling(mine, ref):
    """True if the two files differ only in how a float was spelled.

    MicroPython's float ``repr`` is reproduced in ``mpy_format.float_repr``,
    which documents where it cannot match the oracle: the oracle scales by a
    power of ten in x86 ``long double``, and for some values that lands on
    the other of two 16-digit spellings that both read back as the same
    double.  That is a spelling difference, not a value difference, so this
    checks exactly that -- the texts must parse to the same float, and
    everything else about the two files must be identical.
    """
    a = MPY.decode(mine)
    b = MPY.decode(ref)
    if (a.qstrs != b.qstrs or a.small_int_bits != b.small_int_bits
            or len(a.objs) != len(b.objs)):
        return False
    if MPY.encode_raw_code_bytes(a.raw_code) != \
            MPY.encode_raw_code_bytes(b.raw_code):
        return False
    differing = 0
    for i in range(len(a.objs)):
        x = a.objs[i]
        y = b.objs[i]
        if x == y:
            continue
        if x.kind != MPY.OBJ_FLOAT or y.kind != MPY.OBJ_FLOAT:
            return False
        if float(x.payload) != float(y.payload):
            return False
        differing += 1
    return differing > 0


def describe_difference(mine, ref):
    """Where two ``.mpy`` files first disagree, in terms of the format."""
    a = MPY.decode(mine)
    b = MPY.decode(ref)
    ta = [qstrtable.entry_text(e) for e in a.qstrs]
    tb = [qstrtable.entry_text(e) for e in b.qstrs]
    if ta != tb:
        for i in range(max(len(ta), len(tb))):
            x = ta[i] if i < len(ta) else None
            y = tb[i] if i < len(tb) else None
            if x != y:
                return "qstr %d: ours %r, oracle %r" % (i, x, y)
    if a.objs != b.objs:
        for i in range(max(len(a.objs), len(b.objs))):
            x = a.objs[i] if i < len(a.objs) else None
            y = b.objs[i] if i < len(b.objs) else None
            if x != y:
                return "constant %d: ours %r, oracle %r" % (i, x, y)
    wa = flatten_raw_code(a.raw_code)
    wb = flatten_raw_code(b.raw_code)
    if len(wa) != len(wb):
        return "%d functions, oracle has %d" % (len(wa), len(wb))
    for i in range(len(wa)):
        if wa[i][1].fun_data != wb[i][1].fun_data:
            return ("function %s: %d bytes, oracle has %d"
                    % (wa[i][0], len(wa[i][1].fun_data),
                       len(wb[i][1].fun_data)))
    return "tables and functions agree, so the container framing differs"


def flatten_raw_code(rc):
    """Every raw code in the tree with a path label, iteratively."""
    out = []
    stack = [("0", rc)]
    while stack:
        label, node = stack.pop()
        out.append((label, node))
        i = len(node.children) - 1
        while i >= 0:
            stack.append((label + ".%d" % i, node.children[i]))
            i -= 1
    return out


def find_py(roots):
    out = []
    for root in roots:
        if os.path.isfile(root):
            out.append(root)
            continue
        for d, _dirs, files in os.walk(root):
            for f in sorted(files):
                if f.endswith(".py"):
                    out.append(os.path.join(d, f))
    out.sort()
    return out


def run_corpus(mpy_cross, roots, show):
    tmp = tempfile.mkdtemp()
    out_path = os.path.join(tmp, "oracle.mpy")
    exact = 0
    differ = 0
    float_spelling = 0
    deferred = 0
    rejected_both = 0
    wrongly_accepted = 0
    crashed = 0
    shown = 0
    for path in find_py(roots):
        # mpy-cross keys the module's first qstr on the name it is given, and
        # that name is interned before lexing, so pass the bare basename and
        # use the same one ourselves.
        name = os.path.basename(path)
        proc = subprocess.run([mpy_cross, "-o", out_path, "-s", name, path],
                              capture_output=True)
        with open(path, "rb") as f:
            src = f.read()

        if proc.returncode != 0:
            # The oracle refused the file; so must we.
            try:
                compile_ours(name, src)
            except NotImplementedFeature:
                deferred += 1
            except (P.ParseError, C.CompileError):
                rejected_both += 1
            except Exception as e:  # noqa: BLE001
                # Still a rejection, but not one we meant to make.
                crashed += 1
                if shown < show:
                    shown += 1
                    print("CRASH on a file the oracle rejects: %s\n%s"
                          % (path, traceback.format_exc()))
            else:
                wrongly_accepted += 1
                if shown < show:
                    shown += 1
                    err = proc.stderr.decode("utf-8", "replace").strip()
                    print("ACCEPTED a file the oracle rejects: %s\n  %s"
                          % (path, err.splitlines()[-1] if err else ""))
            continue

        with open(out_path, "rb") as f:
            ref = f.read()
        try:
            mine = compile_ours(name, src)
        except NotImplementedFeature as e:
            deferred += 1
            continue
        except Exception as e:  # noqa: BLE001
            crashed += 1
            if shown < show:
                shown += 1
                print("CRASH: %s\n%s" % (path, traceback.format_exc()))
            continue
        if mine == ref:
            exact += 1
        elif only_float_spelling(mine, ref):
            float_spelling += 1
        else:
            differ += 1
            if shown < show:
                shown += 1
                try:
                    why = describe_difference(mine, ref)
                except Exception as e:  # noqa: BLE001
                    why = "could not decode: %r" % (e,)
                print("DIFFER: %s\n  %s" % (path, why))

    total = exact + differ + float_spelling + deferred + rejected_both \
        + wrongly_accepted + crashed
    print("corpus: %d files" % total)
    print("  byte-exact            : %d" % exact)
    print("  float spelling only   : %d" % float_spelling)
    print("  differ                : %d" % differ)
    print("  rejected by both      : %d" % rejected_both)
    print("  not implemented yet   : %d" % deferred)
    print("  accepted but shouldn't: %d" % wrongly_accepted)
    print("  crashed               : %d" % crashed)
    check(crashed == 0, "no file crashes the compiler")
    check(wrongly_accepted == 0, "nothing the oracle rejects is accepted")
    check(differ == 0, "every file compiles byte-for-byte, or differs only "
          "in how a float is spelled")
    return exact, differ


def test_modes(mpy_cross):
    """"exec" is what mpy-cross does; "eval" and "single" just have to work.

    The oracle has no switch for the other two input kinds, so there is
    nothing to compare them against; what is checked here is that they
    produce a well-formed ``.mpy`` whose module code returns a value.
    """
    for mode, src in (("eval", "1 + 2"), ("single", "x = 1\n")):
        data = C.compile_source(src, "<stdin>", mode)
        mpy = MPY.decode(data)
        check(MPY.encode(mpy) == data,
              "mode %r produces a well-formed .mpy" % mode)
        check(len(mpy.raw_code.fun_data) > 0,
              "mode %r emits some code" % mode)


def test_errors():
    """A few errors the walker itself is responsible for."""
    cases = (
        ("1 = 2\n", "can't assign to expression"),
        ("del 1\n", "can't delete expression"),
        ("break\n", "'break'/'continue' outside loop"),
        ("return 1\n", "'return' outside function"),
        ("yield 1\n", "'yield' outside function"),
        ("def f(a=1, b): pass\n",
         "non-default argument follows default argument"),
        ("def f(a, a): pass\n", "argument name reused"),
        ("f(**a, b)\n", "positional arg after **"),
        ("f(b=1, *a)\n", "* arg after kwarg"),
        ("nonlocal x\n", "can't declare nonlocal in outer code"),
        ("def f():\n    nonlocal x\n", "no binding for nonlocal found"),
        ("{1: 2, 3}\n", "expecting key:value for dict"),
        ("{1, 2: 3}\n", "expecting just a value for set"),
        ("*x = 1\n", "can't assign to expression"),
        ("y = *x\n", "*x must be assignment target"),
        ("a, *b, *c = x\n", "multiple *x in assignment"),
    )
    for src, msg in cases:
        try:
            C.compile_source(src, "t.py")
        except C.CompileError as e:
            check(e.msg == msg,
                  "%r reports %r (got %r)" % (src, msg, e.msg))
        except P.ParseError as e:
            check(False, "%r was rejected by the parser: %s" % (src, e))
        else:
            check(False, "%r should not compile" % src)


def main(argv):
    mpy_cross = None
    show = 10
    roots = []
    it = iter(argv)
    for a in it:
        if a == "--mpy-cross":
            mpy_cross = next(it)
        elif a == "--show":
            show = int(next(it))
        else:
            roots.append(a)

    test_errors()
    test_modes(mpy_cross)
    if mpy_cross and roots:
        run_corpus(mpy_cross, roots, show)
    else:
        print("corpus: none given (pass --mpy-cross PATH CORPUS...)")

    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all compiler checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
