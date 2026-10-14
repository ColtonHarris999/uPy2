"""Round-trip test for the bytecode prelude and line-number encoding.

The emitter cannot be driven end to end until the compiler walker exists, but
its header encoding can be checked now, and against real data: take every
``fun_data`` blob out of the oracle's own ``.mpy`` files, decode the prelude,
re-encode it, and require the bytes to match.  That exercises the two
bit-interleaved headers, the qstr-indexed name section and both line-info
formats over whatever shapes the corpus contains.

    python3 tests/test_bytecode.py --mpy-cross PATH CORPUS...
    micropython tests/test_bytecode.py --mpy DIR_OF_MPY
"""
import os
import sys

# MicroPython has no os.path, so use plain relative entries as the other
# device-runnable tests do.
sys.path.insert(0, ".")
sys.path.insert(0, "..")
from mpyc import bytecode as B  # noqa: E402
from mpyc import mpy_format  # noqa: E402

_fails = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def test_sig_round_trip():
    """Every signature shape, including the multi-byte extensions."""
    cases = []
    for n_state in (1, 2, 16, 17, 100, 1000):
        for n_exc in (0, 1, 2, 7):
            for flags in (0, 1, 0x0F):
                for a in (0, 1, 3, 4, 20):
                    cases.append((n_state, n_exc, flags, a, 0, 0))
    cases.append((1, 0, 0, 0, 0, 0))
    cases.append((300, 15, 0x0F, 40, 7, 9))
    for n_state, n_exc, flags, a, k, d in cases:
        out = bytearray()
        B.encode_prelude_sig(n_state, n_exc, flags, a, k, d, out)
        got = B.decode_prelude_sig(bytes(out), 0)
        check(got[:6] == (n_state, n_exc, flags, a, k, d)
              and got[6] == len(out),
              "sig round-trips for %r (got %r)"
              % ((n_state, n_exc, flags, a, k, d), got))


def test_size_round_trip():
    for n_info in (1, 2, 63, 64, 100, 5000):
        for n_cell in (0, 1, 2, 63, 64, 300):
            out = bytearray()
            B.encode_prelude_size(n_info, n_cell, out)
            i, c, pos = B.decode_prelude_size(bytes(out), 0)
            check((i, c, pos) == (n_info, n_cell, len(out)),
                  "size round-trips for (%d, %d) -> (%d, %d)"
                  % (n_info, n_cell, i, c))


def test_line_info_round_trip():
    """A delta may need several entries; decoding then re-encoding the
    entries must reproduce the bytes."""
    for b, l in ((0, 1), (1, 0), (5, 2), (31, 3), (40, 1), (0, 6), (2, 7),
                 (15, 100), (15, 0x7FF), (200, 0), (0, 2000), (1000, 1000)):
        out = bytearray()
        B.encode_line_info(b, l, out)
        entries, pos = B.decode_line_info(bytes(out), 0, len(out))
        check(pos == len(out), "line info consumes its bytes for (%d,%d)" % (b, l))
        total_b = 0
        total_l = 0
        for eb, el in entries:
            total_b += eb
            total_l += el
        check((total_b, total_l) == (b, l),
              "line info preserves the delta (%d,%d) -> (%d,%d)"
              % (b, l, total_b, total_l))
        again = bytearray()
        for eb, el in entries:
            B._emit_line_entry(eb, el, again)
        check(bytes(again) == bytes(out),
              "line entries re-encode identically for (%d,%d)" % (b, l))


def walk_raw_codes(rc, out):
    """Every raw code in the tree, iteratively."""
    stack = [rc]
    while stack:
        cur = stack.pop()
        out.append(cur)
        for child in reversed(cur.children):
            stack.append(child)
    return out


def test_corpus(paths):
    """Decode and re-encode the prelude of every function in the corpus."""
    files = 0
    funcs = 0
    bad = 0
    for path in paths:
        with open(path, "rb") as f:
            data = f.read()
        try:
            mpy = mpy_format.decode(data)
        except mpy_format.MpyError as e:
            print("skip %s: %s" % (path, e))
            continue
        files += 1
        for rc in walk_raw_codes(mpy.raw_code, []):
            funcs += 1
            try:
                p = B.decode_prelude(rc.fun_data)
                again = B.encode_prelude(p)
            except Exception as e:  # noqa: BLE001
                print("FAIL %s: %r" % (path, e))
                bad += 1
                continue
            if again != rc.fun_data[:p.code_offset]:
                print("FAIL %s: prelude %d bytes differs" % (path, p.code_offset))
                print("   orig: %s" % rc.fun_data[:p.code_offset].hex())
                print("   mine: %s" % again.hex())
                bad += 1
    print("corpus: %d files, %d functions, %d prelude mismatches"
          % (files, funcs, bad))
    check(bad == 0, "every prelude round-trips")


def find_mpy(root):
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        if hasattr(os, "ilistdir"):
            for entry in os.ilistdir(d):
                p = d + "/" + entry[0]
                if entry[1] & 0x4000:
                    stack.append(p)
                elif entry[0].endswith(".mpy"):
                    out.append(p)
        else:
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isdir(p):
                    stack.append(p)
                elif name.endswith(".mpy"):
                    out.append(p)
    out.sort()
    return out


def main(argv):
    mpy_dir = None
    mpy_cross = None
    sources = []
    it = iter(argv)
    for a in it:
        if a == "--mpy":
            mpy_dir = next(it)
        elif a == "--mpy-cross":
            mpy_cross = next(it)
        else:
            sources.append(a)

    test_sig_round_trip()
    test_size_round_trip()
    test_line_info_round_trip()

    paths = []
    if mpy_dir:
        paths = find_mpy(mpy_dir)
    elif mpy_cross and sources:
        # Host-only: needs subprocess, os.walk and os.path.
        import subprocess
        import tempfile
        tmp = tempfile.mkdtemp()
        n = 0
        for src in sources:
            for root, dirs, fs in os.walk(src) if os.path.isdir(src) else [
                    (os.path.dirname(src), [], [os.path.basename(src)])]:
                for f in sorted(fs):
                    if not f.endswith(".py"):
                        continue
                    out = os.path.join(tmp, "m%d.mpy" % n)
                    p = subprocess.run([mpy_cross, "-o", out,
                                        os.path.join(root, f)],
                                       capture_output=True)
                    if p.returncode == 0:
                        paths.append(out)
                        n += 1
    if paths:
        test_corpus(paths)
    else:
        print("corpus: none given (pass --mpy DIR or --mpy-cross PATH SRC...)")

    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all bytecode prelude checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
