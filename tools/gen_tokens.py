"""Generate ``mpyc/tokens.py``: token kinds and the keyword table.

Both come out of the pinned tree and both are configuration dependent, which
is why they are generated rather than written by hand:

* the ``mp_token_kind_t`` enum in ``py/lexer.h`` has ``MP_TOKEN_MALFORMED_FSTRING``
  under ``MICROPY_PY_FSTRINGS`` and ``MP_TOKEN_KW_ASYNC`` / ``MP_TOKEN_KW_AWAIT``
  under ``MICROPY_PY_ASYNC_AWAIT``, so the *numbers* shift with the build;
* ``tok_kw[]`` in ``py/lexer.c`` is indexed from ``MP_TOKEN_KW_FALSE``, so the
  keyword list has to stay in lockstep with the enum.

Numbers matter beyond the lexer: a token leaf in the parse tree stores the
kind, and the grammar tables reference them.

    python3 tools/gen_tokens.py /path/to/micropython-v1.29.0

Defaults match the pinned mpy-cross build (f-strings, t-strings, async/await
and unicode strings all enabled); override with --no-fstrings etc. if the
target config ever differs.
"""
import os
import re
import sys


def parse_enum(path, flags):
    """Token names in order, honouring the #if blocks we know about."""
    with open(path, "r") as f:
        text = f.read()
    m = re.search(r"typedef enum _mp_token_kind_t \{(.*?)\} mp_token_kind_t;",
                  text, re.S)
    if not m:
        raise SystemExit("could not find mp_token_kind_t in %s" % path)
    names = []
    # Stack of "are we currently emitting" states for nested #if blocks.
    emitting = [True]
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#if"):
            cond = line[3:].strip()
            value = flags.get(cond)
            if value is None:
                raise SystemExit("unknown preprocessor condition %r in the "
                                 "token enum; add it to --flags" % cond)
            emitting.append(emitting[-1] and value)
            continue
        if line.startswith("#else"):
            emitting[-1] = emitting[-2] and not emitting[-1]
            continue
        if line.startswith("#endif"):
            emitting.pop()
            continue
        if not emitting[-1]:
            continue
        tok = re.match(r"(MP_TOKEN_\w+)\s*,?\s*(?://.*)?$", line)
        if tok:
            names.append(tok.group(1))
    return names


def parse_keywords(path, flags):
    """``tok_kw[]`` from py/lexer.c, in order."""
    with open(path, "r") as f:
        text = f.read()
    m = re.search(r"static const char \*const tok_kw\[\] = \{(.*?)\n\};",
                  text, re.S)
    if not m:
        raise SystemExit("could not find tok_kw[] in %s" % path)
    words = []
    emitting = [True]
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#if"):
            cond = line[3:].strip()
            value = flags.get(cond)
            if value is None:
                raise SystemExit("unknown condition %r in tok_kw[]" % cond)
            emitting.append(emitting[-1] and value)
            continue
        if line.startswith("#else"):
            emitting[-1] = emitting[-2] and not emitting[-1]
            continue
        if line.startswith("#endif"):
            emitting.pop()
            continue
        if not emitting[-1]:
            continue
        w = re.match(r'"((?:[^"\\]|\\.)*)"\s*,?$', line)
        if w:
            words.append(w.group(1))
    return words


def render(names, keywords, flags, tree_root):
    # MP_TOKEN_NUMBER_OF is the enum's count sentinel, not a token: keep its
    # value as TOKEN_COUNT but leave it out of the name table so no real kind
    # can ever be confused with it.
    if names and names[-1] == "MP_TOKEN_NUMBER_OF":
        names = names[:-1]
    idx = {}
    for i, n in enumerate(names):
        idx[n] = i

    # tok_kw[] is indexed from MP_TOKEN_KW_FALSE, so the keyword list must
    # line up with the enum exactly.
    base = idx.get("MP_TOKEN_KW_FALSE")
    if base is None:
        raise SystemExit("MP_TOKEN_KW_FALSE missing from the enum")
    for i, word in enumerate(keywords):
        kind = base + i
        if kind >= len(names):
            raise SystemExit("keyword %r at %d runs past the enum" % (word, kind))
    # Cross-check: the keyword count must match the span of KW_ tokens.
    kw_names = [n for n in names if n.startswith("MP_TOKEN_KW_")]
    if len(kw_names) != len(keywords):
        raise SystemExit("enum has %d KW_ tokens but tok_kw[] has %d entries"
                         % (len(kw_names), len(keywords)))
    # tok_kw must be sorted by byte comparison: the lexer stops searching on
    # the first entry that compares greater.
    for a, b in zip(keywords, keywords[1:]):
        if not a.encode() < b.encode():
            raise SystemExit("tok_kw[] is not sorted: %r before %r" % (a, b))

    lines = []
    w = lines.append
    w('"""Token kinds and keywords -- GENERATED, do not edit.')
    w("")
    w("Regenerate with ``tools/gen_tokens.py <micropython-source-tree>``.")
    w("")
    w("The numbering is this build's: the enum in ``py/lexer.h`` is conditional,")
    w("so these values are only valid for the configuration recorded below.")
    w('"""')
    w("")
    w("# Generated from %s" % os.path.basename(os.path.abspath(tree_root)))
    w("# Configuration:")
    for k in sorted(flags):
        w("#   %s = %d" % (k, 1 if flags[k] else 0))
    w("")
    for i, n in enumerate(names):
        w("%s = %d" % (n, i))
    w("")
    w("# MP_TOKEN_NUMBER_OF in py/lexer.h: one past the last real token.")
    w("TOKEN_COUNT = %d" % len(names))
    w("MP_TOKEN_NUMBER_OF = TOKEN_COUNT")
    w("")
    w("# kind -> name, for diagnostics and for diffing against the oracle")
    w("TOKEN_NAMES = (")
    for n in names:
        w("    %r," % n)
    w(")")
    w("")
    w("# tok_kw[] from py/lexer.c: sorted, and indexed from MP_TOKEN_KW_FALSE.")
    w("KEYWORDS = (")
    for word in keywords:
        w("    %r," % word)
    w(")")
    w("")
    w("KEYWORD_BASE = MP_TOKEN_KW_FALSE")
    w("")
    w("")
    w("def keyword_kind(text):")
    w('    """The token kind for keyword *text*, or None if it is not one.')
    w("")
    w("    Mirrors the lexer's loop: walk the sorted table and stop as soon as")
    w("    an entry compares greater, so this is O(len(table)) at worst and")
    w("    needs no dict.")
    w('    """')
    w("    i = 0")
    w("    n = len(KEYWORDS)")
    w("    while i < n:")
    w("        kw = KEYWORDS[i]")
    w("        if kw == text:")
    w("            return KEYWORD_BASE + i")
    w("        if kw > text:")
    w("            return None")
    w("        i += 1")
    w("    return None")
    w("")
    w("")
    w("def token_name(kind):")
    w("    if 0 <= kind < TOKEN_COUNT:")
    w("        return TOKEN_NAMES[kind]")
    w('    return "MP_TOKEN_?%d" % kind')
    return "\n".join(lines) + "\n"


def main(argv):
    flags = {
        "MICROPY_PY_FSTRINGS": True,
        "MICROPY_PY_TSTRINGS": True,
        "MICROPY_PY_ASYNC_AWAIT": True,
        "MICROPY_PY_BUILTINS_STR_UNICODE": True,
    }
    tree_root = None
    out_path = None
    it = iter(argv)
    for a in it:
        if a.startswith("--no-"):
            key = "MICROPY_PY_" + a[5:].upper().replace("-", "_")
            if key not in flags:
                raise SystemExit("unknown flag %s" % a)
            flags[key] = False
        elif a == "-o":
            out_path = next(it)
        else:
            tree_root = a
    if tree_root is None:
        print(__doc__)
        return 2
    if out_path is None:
        out_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mpyc", "tokens.py")

    names = parse_enum(os.path.join(tree_root, "py", "lexer.h"), flags)
    keywords = parse_keywords(os.path.join(tree_root, "py", "lexer.c"), flags)
    text = render(names, keywords, flags, tree_root)
    with open(out_path, "w") as f:
        f.write(text)
    real = [n for n in names if n != "MP_TOKEN_NUMBER_OF"]
    print("token kinds : %d (MP_TOKEN_END=0 .. %s)" % (len(real), real[-1]))
    print("keywords    : %d, sorted, based at MP_TOKEN_KW_FALSE" % len(keywords))
    print("wrote       : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
