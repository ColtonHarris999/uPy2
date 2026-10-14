"""Tests for the static qstr pool and the per-module qstr table.

The unit checks run anywhere.  The differential check needs a corpus of
``.mpy`` files built by the pinned ``mpy-cross`` and validates the pool
boundary against the oracle's own decisions: for every qstr the oracle wrote
*inline*, our pool must agree it is not static -- otherwise we would wrongly
write an index -- and for every qstr it wrote *as an index*, our pool must
agree on both the text and the number.

    python3 tests/test_qstrs.py [--corpus DIR_OF_MPY]
    micropython tests/test_qstrs.py
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import mpy_format, qstrs, qstrtable  # noqa: E402

_fails = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def test_pool_shape():
    check(qstrs.STATIC_POOL_SIZE == 166, "pool holds 166 entries")
    check(qstrs.QSTR_LAST_STATIC == 165, "last static qstr is 165")
    check(len(qstrs.STATIC_QSTRS) == qstrs.STATIC_POOL_SIZE,
          "table length matches STATIC_POOL_SIZE")
    check(qstrs.STATIC_QSTRS[qstrs.QSTR_LAST_STATIC] == "zip",
          "the boundary entry is 'zip' (MP_QSTR_zip)")
    check(qstrs.is_static(0) and qstrs.is_static(165),
          "0 and 165 are static")
    check(not qstrs.is_static(166) and not qstrs.is_static(-1),
          "166 and -1 are not static")


def test_null_vs_empty():
    # Index 0 is MP_QSTRnull, a sentinel; index 1 is the real empty string.
    # Both have text "", so a naive text->index map would make "" mean 0 and
    # silently emit the wrong qstr.
    check(qstrs.STATIC_QSTRS[0] == "", "index 0 text is empty")
    check(qstrs.STATIC_QSTRS[1] == "", "index 1 text is empty")
    check(qstrs.static_index("") == 1,
          "interning the empty string yields 1, not the 0 sentinel")
    check(qstrs.QSTR_NULL == 0 and qstrs.QSTR_EMPTY == 1,
          "the null and empty constants")


def test_known_members():
    # Spot checks drawn from the pool, including the punctuation entries.
    for text in ("__dir__", "\n", " ", "*", "/", "<module>", "_", "self",
                 "zip", "range", "__name__", "__qualname__"):
        n = qstrs.static_index(text)
        check(n is not None, "%r is static" % text)
        if n is not None:
            check(qstrs.static_text(n) == text,
                  "%r round-trips through its number" % text)
    # Things that are emphatically not in the static pool.
    for text in ("<listcomp>", "my_variable_name", "numpy",
                 "a_name_nobody_would_reserve"):
        check(qstrs.static_index(text) is None, "%r is not static" % text)


def test_index_consistency():
    # Every entry but the sentinel must map back to its own number.
    for i in range(1, qstrs.STATIC_POOL_SIZE):
        text = qstrs.static_text(i)
        check(qstrs.static_index(text) == i,
              "entry %d (%r) maps back to itself" % (i, text))


def test_module_table():
    t = qstrtable.QstrTable()
    check(t.index("foo") == 0, "first qstr gets index 0")
    check(t.index("bar") == 1, "second qstr gets index 1")
    check(t.index("foo") == 0, "a repeat returns the same index")
    check(len(t) == 2, "repeats do not grow the table")
    check(t.text(1) == "bar", "text() reads back")

    # Static and dynamic share the table, in first-use order.
    t2 = qstrtable.QstrTable()
    for text in ("mymod.py", "<module>", "<listcomp>", "self"):
        t2.index(text)
    entries = t2.entries()
    check(len(entries) == 4, "four entries")
    check(isinstance(entries[0], bytes) and entries[0] == b"mymod.py",
          "a non-static entry is written as text")
    check(isinstance(entries[1], mpy_format.StaticQstr),
          "'<module>' is written as a static index")
    check(isinstance(entries[2], bytes),
          "'<listcomp>' is written as text, unlike '<module>'")
    check(isinstance(entries[3], mpy_format.StaticQstr), "'self' is static")
    check(entries[1].index == qstrs.static_index("<module>"),
          "the static index matches the pool")
    # entry_text inverts entries() for both kinds.
    for text, entry in zip(t2.texts(), entries):
        check(qstrtable.entry_text(entry) == text,
              "entry_text round-trips %r" % text)


def test_encoding_round_trip():
    # A table must survive encode_qstr/decode_qstr unchanged, including the
    # empty string and non-ASCII text.
    t = qstrtable.QstrTable()
    for text in ("", "self", "café", "x" * 300, "<module>"):
        t.index(text)
    for text, entry in zip(t.texts(), t.entries()):
        buf = bytearray()
        mpy_format.encode_qstr(entry, buf)
        back, pos = mpy_format.decode_qstr(bytes(buf), 0)
        check(pos == len(buf), "qstr %r consumes its whole encoding" % text)
        check(qstrtable.entry_text(back) == text,
              "qstr %r survives encode/decode" % text)


def _find_mpy(root):
    """List .mpy files under *root*.

    MicroPython has no os.walk, so descend with an explicit stack using
    ilistdir's type field where available.
    """
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        if hasattr(os, "ilistdir"):
            for entry in os.ilistdir(d):
                name = entry[0]
                is_dir = (entry[1] & 0x4000) != 0
                path = d + "/" + name
                if is_dir:
                    stack.append(path)
                elif name.endswith(".mpy"):
                    out.append(path)
        else:
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if os.path.isdir(path):
                    stack.append(path)
                elif name.endswith(".mpy"):
                    out.append(path)
    out.sort()
    return out


def test_against_corpus(corpus_dir):
    """The pool boundary, checked against the oracle's own output."""
    files = _find_mpy(corpus_dir)
    if not files:
        print("corpus: no .mpy files under %s, skipping" % corpus_dir)
        return
    n_static = 0
    n_dynamic = 0
    bad = 0
    for path in files:
        with open(path, "rb") as fh:
            data = fh.read()
        mpy = mpy_format.decode(data)
        for entry in mpy.qstrs:
            if isinstance(entry, mpy_format.StaticQstr):
                n_static += 1
                i = entry.index
                if not qstrs.is_static(i):
                    print("FAIL %s: static index %d is outside our pool"
                          % (path, i))
                    bad += 1
                    continue
                text = qstrs.static_text(i)
                if qstrs.static_index(text) != i:
                    print("FAIL %s: pool disagrees on %r (%d)"
                          % (path, text, i))
                    bad += 1
            else:
                n_dynamic += 1
                text = entry.decode("utf-8")
                # The oracle spells a qstr out only when it is NOT static.
                # If our pool claims otherwise, our pool is wrong.
                n = qstrs.static_index(text)
                if n is not None:
                    print("FAIL %s: oracle wrote %r inline but our pool "
                          "calls it static (%d)" % (path, text, n))
                    bad += 1
    print("corpus: %d files, %d static refs, %d inline qstrs, %d disagreements"
          % (len(files), n_static, n_dynamic, bad))
    check(bad == 0, "pool agrees with the oracle on every qstr")


def test_memory():
    """Report what the pool costs, since it is a constant bite of the budget."""
    try:
        import gc
        gc.mem_free
    except (ImportError, AttributeError):
        # CPython's gc has no mem_free; this measurement is MicroPython-only.
        print("pool memory: (skipped, no gc.mem_free)")
        return
    gc.collect()
    before = gc.mem_free()
    # Rebuild an equivalent table so the measurement does not depend on
    # whether the module was already imported.
    table = tuple(qstrs.STATIC_QSTRS)
    index = {}
    for i in range(1, len(table)):
        index[table[i]] = i
    gc.collect()
    after = gc.mem_free()
    raw = 0
    for t in qstrs.STATIC_QSTRS:
        raw += len(t)
    print("pool memory: %d bytes as objects, %d bytes of actual text"
          % (before - after, raw))
    print("             (packing the texts into one string plus an offset")
    print("              array would cut this to roughly %d bytes)"
          % (raw + 2 * len(table)))
    check(len(index) == len(table) - 1, "the rebuilt index has one entry per non-sentinel qstr")


def main(argv):
    corpus = None
    it = iter(argv)
    for a in it:
        if a == "--corpus":
            corpus = next(it)
    test_pool_shape()
    test_null_vs_empty()
    test_known_members()
    test_index_consistency()
    test_module_table()
    test_encoding_round_trip()
    test_memory()
    if corpus:
        test_against_corpus(corpus)
    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all qstr checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
