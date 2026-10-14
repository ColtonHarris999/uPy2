"""Rolling digest of a parse tree, for cross-runtime comparison.

tests/test_parser.py needs subprocess to drive the C oracle, which MicroPython
lacks.  This reduces whole trees to one order-sensitive digest so the same
files can run under CPython and MicroPython v1.29.0 and be compared directly.

    python3 tests/parse_digest.py FILE.py...
    micropython -X heapsize=256M tests/parse_digest.py FILE.py...

A corpus of several hundred files held in one process needs far more heap
than one module does, and the unix port's default heap is small; raise it,
or a MemoryError on a large file will read as a difference between the
runtimes.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import grammar as G  # noqa: E402
from mpyc import parser as P  # noqa: E402
from mpyc.lexer import Lexer  # noqa: E402
P.Lexer = Lexer
from mpyc import parsetree as PT  # noqa: E402

def main(argv):
    acc = 0
    nodes = 0
    files = 0
    for path in argv:
        f = open(path, "rb")
        src = f.read()
        f.close()
        try:
            pr = P.Parser(P.Lexer(path, src), P.PARSE_FILE_INPUT)
            tree = pr.parse()
        except Exception:
            continue
        files += 1
        stack = [(tree.root, 0)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if PT.is_struct(node):
                k = tree.struct_kind(node)
                acc = (acc * 31 + k + depth * 7
                       + tree.source_line(node) * 13) & 0xFFFFFFFF
                if k == G.RULE_const_object:
                    for b in repr(tree.const(tree.child(node, 0))).encode():
                        acc = (acc * 31 + b) & 0xFFFFFFFF
                    continue
                n = tree.num_children(node)
                acc = (acc * 31 + n) & 0xFFFFFFFF
                for i in range(n - 1, -1, -1):
                    stack.append((tree.child(node, i), depth + 1))
            else:
                acc = (acc * 31 + (node & 0xFFFF) + depth) & 0xFFFFFFFF
    print("files=%d nodes=%d digest=%08x" % (files, nodes, acc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
