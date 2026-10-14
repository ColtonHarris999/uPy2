"""Generate ``mpyc/qstr_pool.py``: the set of strings a build has interned.

Separate from ``mpyc/qstrs.py``, and for a different reason.  ``qstrs.py``
holds the 166 *static* qstrs, whose numbers are fixed in every build and which
an ``.mpy`` may therefore reference by index.  This holds the **whole** qstr
pool of one particular build, which is build-specific and carries no portable
numbering.

It matters because of one decision in ``push_result_token``: a string literal
longer than ``MICROPY_ALLOC_PARSE_INTERN_STRING_LEN`` (10) becomes a qstr leaf
only if an equal string *is already interned*, and otherwise becomes a
constant object.  Those two produce different bytecode.  So whether
``'__contains__'`` is a leaf depends on the pool of whichever build does the
compiling -- and mpy-cross's pool is not the same as a firmware's.

Only membership is needed, not numbering: non-static qstrs are written to an
``.mpy`` as text, so their numbers never leave the compiler.

    python3 tools/gen_qstr_pool.py /path/to/micropython-v1.29.0 [--build DIR]

The default build is mpy-cross's, since mpy-cross is the byte-exact oracle.
Point ``--build`` at a firmware's build directory to match that target instead.
"""
import ast
import os
import re
import sys

DEFAULT_BUILD = "mpy-cross/build"


def read_pool(path):
    """Every qstr text in a generated header, in order."""
    out = []
    pat = re.compile(r'^QDEF[01]\((MP_QSTR\w*), \d+, (\d+), (".*")\)\s*$')
    with open(path, "r") as f:
        for line in f:
            m = pat.match(line)
            if m:
                out.append(ast.literal_eval(m.group(3)))
    return out


def main(argv):
    tree_root = None
    build = DEFAULT_BUILD
    out_path = None
    it = iter(argv)
    for a in it:
        if a == "--build":
            build = next(it)
        elif a == "-o":
            out_path = next(it)
        else:
            tree_root = a
    if tree_root is None:
        print(__doc__)
        return 2
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if out_path is None:
        out_path = os.path.join(here, "mpyc", "qstr_pool.py")

    header = os.path.join(tree_root, build, "genhdr", "qstrdefs.generated.h")
    if not os.path.exists(header):
        raise SystemExit("no generated header at %s -- build it first" % header)
    pool = read_pool(header)
    if not pool:
        raise SystemExit("no QDEF entries found in %s" % header)

    # Only entries past the static pool are worth storing: the static ones are
    # already in qstrs.py, and membership there is checked first.
    sys.path.insert(0, here)
    from mpyc import qstrs
    static = set()
    for i in range(qstrs.STATIC_POOL_SIZE):
        static.add(qstrs.static_text(i))
    extra = []
    seen = set()
    for text in pool[qstrs.STATIC_POOL_SIZE:]:
        if text in static or text in seen:
            continue
        seen.add(text)
        extra.append(text)

    # Stored as one length-prefixed blob rather than a set of strings: on
    # the target every string object costs a header and a pointer, and a set
    # of 600-odd of them was measured at 31 KB against a 64 KB heap.  The
    # blob is a few kilobytes, and membership is a binary search over it.
    entries = sorted(t.encode("utf-8") for t in extra)
    blob = bytearray()
    for raw in entries:
        if len(raw) > 255:
            raise SystemExit("qstr %r is too long for a one-byte length"
                             % raw[:32])
        blob.append(len(raw))
        blob.extend(raw)

    lines = []
    w = lines.append
    w('"""The non-static part of one build\'s qstr pool -- GENERATED.')
    w("")
    w("Regenerate with ``tools/gen_qstr_pool.py <tree> [--build DIR]``.")
    w("")
    w("A string literal longer than 10 bytes becomes a qstr leaf only if an")
    w("equal string is already interned, and a constant object otherwise --")
    w("different bytecode.  So this pool changes what gets compiled, and it")
    w("is specific to the build it came from: mpy-cross's pool is not a")
    w("firmware's.  Membership is all that is needed; non-static qstrs are")
    w("written to an .mpy as text, so their numbers never escape.")
    w("")
    w("The entries are one sorted, length-prefixed blob, searched by")
    w("bisection.  A set of separate string objects costs roughly five times")
    w("as much on the target, where every object carries a header.")
    w('"""')
    w("")
    w("from array import array")
    w("")
    w("# Source: %s" % build)
    w("COUNT = %d" % len(entries))
    w("")
    w("BLOB = (")
    chunk = bytes(blob)
    step = 24
    for i in range(0, len(chunk), step):
        w("    %r" % chunk[i:i + step])
    w(")")
    w("")
    w("# Offset of each entry's length byte, built once at import.")
    w("_OFFSETS = array('I')")
    w("_pos = 0")
    w("while _pos < len(BLOB):")
    w("    _OFFSETS.append(_pos)")
    w("    _pos += 1 + BLOB[_pos]")
    w("")
    w("")
    w("def entry(i):")
    w('    """Entry *i*, as bytes."""')
    w("    off = _OFFSETS[i]")
    w("    return BLOB[off + 1:off + 1 + BLOB[off]]")
    w("")
    w("")
    w("def contains(raw):")
    w('    """Is *raw* (bytes) in this build\'s pool?"""')
    w("    lo = 0")
    w("    hi = len(_OFFSETS) - 1")
    w("    while lo <= hi:")
    w("        mid = (lo + hi) // 2")
    w("        cur = entry(mid)")
    w("        if cur == raw:")
    w("            return True")
    w("        if cur < raw:")
    w("            lo = mid + 1")
    w("        else:")
    w("            hi = mid - 1")
    w("    return False")
    w("")
    w("")
    w("class Pool:")
    w('    """``raw in pool`` for the interning pool to consult."""')
    w("")
    w("    def __contains__(self, raw):")
    w("        return contains(raw)")
    w("")
    w("    def __len__(self):")
    w("        return COUNT")
    w("")
    w("")
    w("POOL = Pool()")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("build pool      : %d entries total" % len(pool))
    print("past the static : %d (what this file holds)" % len(extra))
    print("blob            : %d bytes" % len(blob))
    print("wrote           : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
