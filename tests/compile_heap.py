"""Measure what a whole compile costs on the target: peak heap per file.

The budget is 64 KB of heap for a 200-line module.  The parse tree is held
live across all four passes and the emitter's tables grow alongside it, so
the peak is somewhere in the middle of the last pass, not at the end -- which
is why this samples ``gc.mem_free`` from inside the compile rather than
measuring before and after.

Two numbers are reported per file, and they answer different questions:

* **live** is what is still reachable at the peak, measured by collecting
  while holding it.  That is what has to fit in the budget.
* **churn** is how much was allocated in total, garbage included.  It is
  transient pressure on the collector, not a budget violation.

    micropython tests/compile_heap.py FILE.py...
    python3 tests/compile_heap.py FILE.py...     (no heap figures)
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import compile as C  # noqa: E402
from mpyc import parser as P  # noqa: E402
from mpyc.lexer import Lexer, NotImplementedFeature  # noqa: E402

try:
    import gc
    gc.mem_free
    HAVE_GC = True
except (ImportError, AttributeError):
    HAVE_GC = False


class Watermark:
    """Samples the heap at every emit pass, and keeps the worst."""

    def __init__(self, base):
        self.base = base
        self.live = 0
        self.churn = 0

    def sample(self):
        if not HAVE_GC:
            return
        used = self.base - gc.mem_free()
        if used > self.churn:
            self.churn = used
        gc.collect()
        used = self.base - gc.mem_free()
        if used > self.live:
            self.live = used


def compile_watched(path, src, mark):
    """Compile *src*, sampling the heap after every pass over every scope."""
    pr = P.Parser(Lexer(path, src))
    tree = pr.parse()
    mark.sample()

    def on_pass(comp, scope, pass_):
        mark.sample()

    return C.compile_to_scopes(tree, path.encode("utf-8"), False, 0, on_pass)


def main(argv):
    if not argv:
        print("usage: compile_heap.py FILE.py...")
        return 2
    rows = []
    worst_live = 0
    worst_path = None
    for path in argv:
        try:
            f = open(path, "rb")
        except OSError:
            print("%-44s missing" % path)
            continue
        src = f.read()
        f.close()
        lines = src.count(b"\n") + 1
        if HAVE_GC:
            gc.collect()
            base = gc.mem_free()
        else:
            base = 0
        mark = Watermark(base)
        try:
            comp, module_scope = compile_watched(path, src, mark)
        except NotImplementedFeature as e:
            print("%-44s skipped: %s" % (path, e))
            continue
        except Exception as e:  # noqa: BLE001
            print("%-44s rejected: %r" % (path, e))
            continue
        n_scopes = len(comp.scopes)
        code = 0
        for scope in comp.scopes:
            if scope.raw_code is not None:
                code += len(scope.raw_code)
        rows.append((path, lines, n_scopes, code, mark.live, mark.churn))
        if mark.live > worst_live:
            worst_live = mark.live
            worst_path = path
        comp = None
        module_scope = None

    print("%-44s %6s %7s %7s %9s %9s"
          % ("file", "lines", "scopes", "code", "live", "churn"))
    for path, lines, n_scopes, code, live, churn in rows:
        print("%-44s %6d %7d %7d %9d %9d"
              % (path[-44:], lines, n_scopes, code, live, churn))
    if HAVE_GC and worst_path is not None:
        print("")
        print("peak live heap: %d bytes (%s)" % (worst_live, worst_path))
        print("budget        : 65536 bytes for a 200-line module")
    elif not HAVE_GC:
        print("")
        print("(no gc.mem_free on this runtime; run under MicroPython for "
              "heap figures)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
