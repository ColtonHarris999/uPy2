"""Rolling digest of the lexer's whole token stream, for cross-runtime checks.

tests/test_lexer.py needs subprocess to drive the C oracle, which MicroPython
does not have.  This instead reduces the entire stream to one order-sensitive
digest, so the same files can be run under CPython and under MicroPython
v1.29.0 and the two numbers compared directly.  Any divergence in kind, line,
column or text changes the digest.

    python3 tests/lexer_digest.py FILE.py...
    micropython -X heapsize=256M tests/lexer_digest.py FILE.py...

A corpus of several hundred files held in one process needs far more heap
than one module does, and the unix port's default heap is small; raise it,
or a MemoryError on a large file will read as a difference between the
runtimes.
"""
import sys
sys.path.insert(0, ".")
from mpyc import lexer as L
from mpyc import tokens as T
total = 0
acc = 0
for path in sys.argv[1:]:
    f = open(path, "rb"); src = f.read(); f.close()
    lex = L.Lexer(path, src)
    while True:
        total += 1
        # order-sensitive rolling digest of the whole stream
        acc = (acc * 31 + lex.tok_kind + lex.tok_line * 7 + lex.tok_column * 13
               + len(lex.vstr) * 17) & 0xFFFFFFFF
        for b in lex.vstr:
            acc = (acc * 31 + b) & 0xFFFFFFFF
        if lex.tok_kind == T.MP_TOKEN_END:
            break
        lex.to_next()
print("files=%d tokens=%d digest=%08x" % (len(sys.argv) - 1, total, acc))
