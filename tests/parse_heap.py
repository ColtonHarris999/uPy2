"""Measure what parsing costs on the target: peak heap and node counts.

The budget is 64 KB of heap for a 200-line module, and the parse tree is the
dominant consumer because all four compiler passes run over it before it can
be freed.  This reports the real figure per file, so the budget stops being a
projection.

    micropython tests/parse_heap.py FILE.py...
    python3 tests/parse_heap.py FILE.py...      (no heap figures)
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import parser as P  # noqa: E402

try:
    import gc
    gc.mem_free
    HAVE_GC = True
except (ImportError, AttributeError):
    HAVE_GC = False


def main(argv):
    if not argv:
        print("usage: parse_heap.py FILE.py...")
        return 2
    worst = 0
    worst_path = None
    rows = []
    for path in argv:
        f = open(path, "rb")
        src = f.read()
        f.close()
        lines = src.count(b"\n") + 1
        if HAVE_GC:
            gc.collect()
            before = gc.mem_free()
        try:
            tree = P.parse(src, path)
        except Exception as e:  # noqa: BLE001
            print("%-44s SKIP %r" % (path.split("/")[-1], e))
            continue
        # Collect while still holding the tree: what remains is the LIVE cost,
        # which is what has to survive all four compiler passes.  Without the
        # collect this would instead be total allocation including garbage,
        # which is far larger and not the budget.
        if HAVE_GC:
            churn = before - gc.mem_free()
            gc.collect()
            used = before - gc.mem_free()
        else:
            churn = used = 0
        nodes = tree.count_nodes()
        arena = tree.bytes_used()
        consts = len(tree.consts)
        rows.append((lines, used, churn, arena, nodes, consts, path))
        if used > worst:
            worst = used
            worst_path = path
        tree = None
        if HAVE_GC:
            gc.collect()
    rows.sort()
    print("%6s %9s %10s %8s %6s %6s  %s"
          % ("lines", "live", "churn", "arena", "nodes", "const", "file"))
    for lines, used, churn, arena, nodes, consts, path in rows:
        print("%6d %9d %10d %8d %6d %6d  %s"
              % (lines, used, churn, arena, nodes, consts,
                 path.split("/")[-1]))
    if HAVE_GC and rows:
        print("")
        print("worst live: %d bytes (%s)" % (worst, worst_path.split("/")[-1]))
        print("budget    : 65536 bytes for a 200-line module")
        print("")
        print("live  = still allocated after a collect, tree held: the figure")
        print("        that must survive all four compiler passes")
        print("churn = allocated during the parse before collecting, i.e.")
        print("        including garbage -- a speed/GC concern, not the budget")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
