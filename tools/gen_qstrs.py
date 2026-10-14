"""Generate ``mpyc/qstrs.py``: MicroPython's static qstr pool.

The pool is the list of strings that get a fixed, build-independent qstr
number.  ``.mpy`` files refer to them by that number instead of spelling them
out (``save_qstr`` in ``py/persistentcode.c`` writes ``index << 1 | 1``), so
the numbering has to be exactly right or every file that mentions a common
name comes out wrong.

The authoritative source is ``static_qstr_list`` in ``py/makeqstrdata.py`` --
a literal Python list.  ``print_qstr_data`` emits ``MP_QSTRnull`` first, then
that list in source order, then every other qstr sorted by value. Only the
first group has stable numbers, which is exactly why a static index means the
same string in any build; the generated ``qstrdefs.generated.h`` of a
*particular* build cannot be trusted past that boundary.

This generator reads the list from the pinned source tree and, when build
directories are present, cross-checks its own output against their generated
headers before writing anything.

    python3 tools/gen_qstrs.py /path/to/micropython-v1.29.0

This is tooling, not shipped code, so it may use ``ast``.
"""
import ast
import os
import re
import sys

HEADER_CANDIDATES = (
    "mpy-cross/build/genhdr/qstrdefs.generated.h",
    "ports/unix/build-standard/genhdr/qstrdefs.generated.h",
)


def read_static_list(tree_root):
    """The ``static_qstr_list`` literal from ``py/makeqstrdata.py``."""
    path = os.path.join(tree_root, "py", "makeqstrdata.py")
    with open(path, "r") as f:
        src = f.read()
    mod = ast.parse(src, path)
    for node in mod.body:
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", None) == "static_qstr_list"):
            return ast.literal_eval(node.value)
    raise SystemExit("could not find static_qstr_list in %s" % path)


def read_generated_header(path):
    """``(identifier, byte_length, text)`` for each QDEF line, in order."""
    out = []
    pat = re.compile(r'^QDEF[01]\((MP_QSTR\w*), \d+, (\d+), (".*")\)\s*$')
    with open(path, "r") as f:
        for line in f:
            m = pat.match(line)
            if m:
                out.append((m.group(1), int(m.group(2)),
                            ast.literal_eval(m.group(3))))
    return out


def cross_check(tree_root, pool):
    """Verify *pool* against every generated header we can find.

    Returns the list of headers checked.  A header's entries past the static
    boundary are ignored: they are build-specific by design.
    """
    checked = []
    for rel in HEADER_CANDIDATES:
        path = os.path.join(tree_root, rel)
        if not os.path.exists(path):
            continue
        entries = read_generated_header(path)
        if len(entries) < len(pool):
            raise SystemExit("%s has only %d entries, need %d"
                             % (path, len(entries), len(pool)))
        for i, text in enumerate(pool):
            ident, blen, got = entries[i]
            if got != text:
                raise SystemExit("%s entry %d is %r, expected %r"
                                 % (path, i, got, text))
            if blen != len(text.encode("utf-8")):
                raise SystemExit("%s entry %d length %d != %d"
                                 % (path, i, blen, len(text.encode("utf-8"))))
        # The entry just past the boundary must be MP_QSTR_zip's successor,
        # i.e. the boundary itself must be zip.
        if entries[len(pool) - 1][0] != "MP_QSTR_zip":
            raise SystemExit("%s: qstr %d is %s, expected MP_QSTR_zip"
                             % (path, len(pool) - 1, entries[len(pool) - 1][0]))
        checked.append(rel)
    return checked


def render(pool, tree_root, checked):
    # Index 0 is MP_QSTRnull, a sentinel whose text is also "" -- so the
    # text->index map must skip it and let index 1 own the empty string.
    seen = {}
    dups = []
    for i, text in enumerate(pool):
        if i == 0:
            continue
        if text in seen:
            dups.append((seen[text], i, text))
        else:
            seen[text] = i
    if dups:
        raise SystemExit("duplicate texts in the static pool: %r" % (dups,))

    lines = []
    w = lines.append
    w('"""MicroPython\'s static qstr pool -- GENERATED, do not edit.')
    w("")
    w("Regenerate with ``tools/gen_qstrs.py <micropython-source-tree>``.")
    w("")
    w("These %d strings have fixed qstr numbers in every MicroPython build, so" % len(pool))
    w("an ``.mpy`` file may refer to them by number rather than spelling them")
    w("out.  ``py/makeqstrdata.py`` emits ``MP_QSTRnull`` and then its literal")
    w("``static_qstr_list``; everything after this pool is sorted per build and")
    w("has no stable number.")
    w("")
    w("Index 0 is ``MP_QSTRnull``, a sentinel, and its text is also the empty")
    w("string -- the real empty string is index 1 (``MP_QSTR_``).  Interning")
    w('"" must therefore yield 1, never 0, which is why the lookup table below')
    w("skips index 0.")
    w('"""')
    w("")
    w("# Source: %s" % os.path.basename(os.path.abspath(tree_root)))
    if checked:
        w("# Cross-checked against: %s" % ", ".join(checked))
    w("")
    w("QSTR_NULL = 0")
    w("QSTR_EMPTY = 1")
    w("QSTR_LAST_STATIC = %d" % (len(pool) - 1))
    w("STATIC_POOL_SIZE = %d" % len(pool))
    w("")
    w("# index -> text; index is the qstr number")
    w("STATIC_QSTRS = (")
    for i, text in enumerate(pool):
        comment = "  # MP_QSTRnull (sentinel)" if i == 0 else ""
        w("    %r,%s" % (text, comment))
    w(")")
    w("")
    w("# text -> qstr number, excluding the index 0 sentinel")
    w("_INDEX = {}")
    w("for _i in range(1, STATIC_POOL_SIZE):")
    w("    _INDEX[STATIC_QSTRS[_i]] = _i")
    w("")
    w("")
    w("def static_index(text):")
    w('    """The fixed qstr number for *text*, or None if it is not static."""')
    w("    return _INDEX.get(text)")
    w("")
    w("")
    w("def is_static(qstr_number):")
    w("    return 0 <= qstr_number <= QSTR_LAST_STATIC")
    w("")
    w("")
    w("def static_text(qstr_number):")
    w('    """The text of a static qstr number."""')
    w("    return STATIC_QSTRS[qstr_number]")
    return "\n".join(lines) + "\n"


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    tree_root = argv[0]
    out_path = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mpyc", "qstrs.py")

    static_list = read_static_list(tree_root)
    pool = [""] + list(static_list)  # index 0 is MP_QSTRnull
    if static_list[-1] != "zip":
        raise SystemExit("static_qstr_list ends with %r, expected 'zip' "
                         "(QSTR_LAST_STATIC)" % static_list[-1])

    checked = cross_check(tree_root, pool)
    text = render(pool, tree_root, checked)
    with open(out_path, "w") as f:
        f.write(text)
    print("static_qstr_list : %d entries" % len(static_list))
    print("pool             : %d entries (qstr 0..%d), boundary MP_QSTR_zip"
          % (len(pool), len(pool) - 1))
    print("cross-checked    : %s" % (", ".join(checked) if checked
                                     else "no build headers found"))
    print("wrote            : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
