"""Rolling digest of compiled output, for cross-runtime comparison.

``tests/test_compile.py`` needs ``subprocess`` to drive mpy-cross, which
MicroPython does not have.  This reduces each file's whole ``.mpy`` to one
digest so the same corpus can be run under CPython and under MicroPython
v1.29.0 and the two compared directly: the compiler has to produce the same
bytes on the machine it runs on as on the machine it was developed on.

    python3 tests/compile_digest.py FILE.py...
    micropython -X heapsize=256M tests/compile_digest.py FILE.py...

A corpus of several hundred files held in one process needs far more heap
than compiling one module does, and the unix port's default heap is small;
raise it, or a MemoryError on a large file will read as a difference between
the runtimes.  ``tests/compile_heap.py`` is what measures the per-module cost
that actually has to fit the budget.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import compile as C  # noqa: E402
from mpyc.lexer import NotImplementedFeature  # noqa: E402


def basename(path):
    i = len(path) - 1
    while i >= 0:
        if path[i] == "/":
            return path[i + 1:]
        i -= 1
    return path


def main(argv):
    acc = 0
    total_bytes = 0
    files = 0
    skipped = 0
    for path in argv:
        f = open(path, "rb")
        src = f.read()
        f.close()
        try:
            data = C.compile_source(src, basename(path))
        except NotImplementedFeature:
            skipped += 1
            continue
        except Exception:  # noqa: BLE001
            # A file the compiler rejects still contributes to the digest, so
            # that the two runtimes must agree on rejections too.
            acc = (acc * 31 + 0xFF) & 0xFFFFFFFF
            files += 1
            continue
        files += 1
        total_bytes += len(data)
        for b in data:
            acc = (acc * 31 + b) & 0xFFFFFFFF
        acc = (acc * 31 + (len(data) & 0xFF)) & 0xFFFFFFFF
    print("files   : %d (%d skipped as not implemented)" % (files, skipped))
    print("bytes   : %d" % total_bytes)
    print("digest  : %08x" % acc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
