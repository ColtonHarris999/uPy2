"""Differential test: mpyc.lexer against MicroPython's own lexer.

The oracle is tools/lexdump.c, which links the real py/lexer.c out of the
pinned mpy-cross build and prints every token it produces.  This runs both
over the same files and compares kind, line, column and text.

    python3 tests/test_lexer.py --lexdump build/lexdump PATH...

Build the oracle first with tools/build_lexdump.sh.

The f-string and t-string rewrites also get an oracle-free check, since
those are the one place the lexer rewrites its input rather than reporting
it, and the expected text is short enough to write down.
"""
import os
import sys

# Plain relative entries, as the other device-runnable tests use: MicroPython
# has no os.path.
sys.path.insert(0, ".")
sys.path.insert(0, "..")
from mpyc import lexer as L  # noqa: E402
from mpyc import tokens as T  # noqa: E402


def oracle_tokens(lexdump, path):
    """[(kind, line, column, text_bytes)] from the C lexer, or ('E', msg).

    ``subprocess`` is imported here rather than at the top so the rewrite
    checks below, which need no oracle, can run under MicroPython too.
    """
    import subprocess
    p = subprocess.run([lexdump, path], capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    toks = []
    for line in out.splitlines():
        if line.startswith("T\t"):
            _, kind, ln, col, hexed = line.split("\t", 4)
            toks.append((int(kind), int(ln), int(col), bytes.fromhex(hexed)))
        elif line.startswith("E\t"):
            return None, line.split("\t", 1)[1]
        elif line == "END":
            break
    return toks, None


def our_tokens(path, limit=200000):
    with open(path, "rb") as f:
        src = f.read()
    lex = L.Lexer(path, src)
    toks = []
    while True:
        toks.append((lex.tok_kind, lex.tok_line, lex.tok_column, bytes(lex.vstr)))
        if lex.tok_kind == T.MP_TOKEN_END:
            break
        if len(toks) > limit:
            raise RuntimeError("runaway lexer (over %d tokens)" % limit)
        lex.to_next()
    return toks


def describe(tok):
    return "%s@%d:%d %r" % (T.token_name(tok[0]), tok[1], tok[2], tok[3])


def compare(lexdump, path, verbose=False):
    ref, err = oracle_tokens(lexdump, path)
    try:
        mine = our_tokens(path)
    except L.NotImplementedFeature as e:
        return "not-implemented", str(e)
    except Exception as e:  # noqa: BLE001
        if ref is None:
            # Both sides refused the input.
            return "both-error", "%s / oracle: %s" % (repr(e), err)
        return "error", repr(e)
    if ref is None:
        return "oracle-error-only", "oracle raised %s, we produced %d tokens" % (
            err, len(mine))
    if mine == ref:
        return "ok", None
    # Report the first divergence.
    n = min(len(mine), len(ref))
    for i in range(n):
        if mine[i] != ref[i]:
            ctx = ""
            if i:
                ctx = "\n    after: %s" % describe(ref[i - 1])
            return "mismatch", ("token %d of %d%s\n    oracle: %s\n    mine  : %s"
                                % (i, len(ref), ctx, describe(ref[i]),
                                   describe(mine[i])))
    return "mismatch", ("length %d vs oracle %d; first extra: %s"
                        % (len(mine), len(ref),
                           describe((mine if len(mine) > n else ref)[n])))


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, fs in os.walk(p):
                for f in sorted(fs):
                    if f.endswith(".py"):
                        yield os.path.join(root, f)
        elif p.endswith(".py"):
            yield p


# What the lexer rewrites each literal into: the text of the token it
# produces, then the source it splices in behind it.  Every line here was
# confirmed token-exact against the C oracle on that same input, so these are
# py/lexer.c's rewrites and not a guess at them; they are written down because
# the rewrite is the one thing the lexer does that reading the resulting token
# stream does not make obvious.
REWRITES = (
    # An f-string becomes a .format() call: the placeholders stay in the
    # literal for str.format to fill, and each expression is wrapped in
    # parentheses so a comma inside it stays one argument.
    ('f"a{x}b"', 'a{}b', '.format((x),)'),
    ('f"{x}{y}"', '{}{}', '.format((x),(y),)'),
    ('f"{x=}"', 'x={}', '.format((x),)'),
    # Even with nothing to interpolate: "{{" is str.format's escape, so the
    # call has to happen for it to be unescaped.
    ('f"{{literal}}"', '{{literal}}', '.format()'),
    # A t-string becomes a __template__ call: the literal pieces as one
    # tuple, then four arguments per interpolation -- the value, the source
    # text of the expression, the conversion, and the format spec.
    ('t"abc"', '__template__', '(("abc",),)'),
    ('t"a{x}b"', '__template__', '(("a","b",),(x),"x",None,"",)'),
    ('t"{x!r}"', '__template__', '(("","",),(x),"x","r","",)'),
    ('t"{x:>10}"', '__template__', '(("","",),(x),"x",None,">10",)'),
    # t'{x=}' repeats the expression in the literal and asks for repr.
    ('t"{x=}"', '__template__', '(("x=","",),(x),"x",\'r\',"",)'),
    # A format spec with braces of its own is handed over as an f-string, to
    # be expanded when this text is re-lexed.
    ('t"{x:>{w}}"', '__template__', '(("","",),(x),"x",None,f">{w}",)'),
    # "{{" is one brace here too, but a t-string unescapes it itself.
    ('t"a{{b}}c"', '__template__', '(("a{b}c",),)'),
)


def test_rewrites():
    """Each literal's first token, and the text spliced in after it."""
    bad = 0
    for src, first_text, injected in REWRITES:
        lex = L.Lexer("t.py", src)
        got_first = bytes(lex.vstr).decode("utf-8", "replace")
        if got_first != first_text:
            print("FAIL %s: first token is %r, expected %r"
                  % (src, got_first, first_text))
            bad += 1
        if injected is not None:
            got = bytes(lex.inject_chrs[:lex.inject_chrs_idx or None])
            # The injected text sits in the buffer ahead of the three-byte
            # lookahead window the lexer rewound into it.
            text = bytes(lex.inject_chrs).decode("utf-8", "replace")
            if lex.inject_chrs_idx >= 3:
                text = text[lex.inject_chrs_idx - 3:]
            # Drop the rewound window, which is the source after the literal.
            text = text[:len(text) - 3] if len(text) >= 3 else text
            if text != injected:
                print("FAIL %s: injected %r, expected %r"
                      % (src, text, injected))
                bad += 1
    print("rewrites: %d literals, %d failures" % (len(REWRITES), bad))
    return bad


def main(argv):
    lexdump = None
    paths = []
    verbose = False
    show = 6
    it = iter(argv)
    for a in it:
        if a == "--lexdump":
            lexdump = next(it)
        elif a == "-v":
            verbose = True
        elif a == "--show":
            show = int(next(it))
        else:
            paths.append(a)
    if not paths:
        # The rewrite checks need no oracle and no os.path, so they run
        # anywhere, including on the device.
        return 1 if test_rewrites() else 0
    if lexdump is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lexdump = os.path.join(here, "build", "lexdump")
    if not os.path.exists(lexdump):
        print("ERROR: no oracle at %s -- run tools/build_lexdump.sh"
              % lexdump)
        return 2

    bad_rewrites = test_rewrites()

    counts = {}
    shown = 0
    for path in iter_files(paths):
        status, detail = compare(lexdump, path, verbose)
        counts[status] = counts.get(status, 0) + 1
        if status != "ok" and shown < show:
            shown += 1
            print("%-18s %s" % (status, path))
            if detail:
                for line in detail.splitlines():
                    print("    %s" % line)
            sys.stdout.flush()
        elif status == "ok" and verbose:
            print("ok                 %s" % path)
    print("summary: " + " ".join("%s=%d" % kv for kv in sorted(counts.items())))
    bad = sum(v for k, v in counts.items()
              if k not in ("ok", "both-error", "not-implemented"))
    return 1 if bad or bad_rewrites else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
