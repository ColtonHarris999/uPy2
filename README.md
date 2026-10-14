# mpyc — a MicroPython compiler, written in MicroPython

Source to bytecode, no interpreter. The compiler is written in the subset of
Python that MicroPython v1.29.0 runs, so it compiles to bytecode and executes
on silicon whose native ISA *is* MicroPython bytecode — where a compiler
written in C has no target.

## Pinned oracles

| role | what |
|---|---|
| **byte-exact oracle** | `mpy-cross` built from MicroPython tag **v1.29.0** (reports `mpy-cross emitting mpy v6.3`) |
| **runtime oracle** | the configured `ports/unix` build at the same tag (`MicroPython v1.29.0`) |

The bytecode matches v1.29.0 exactly — no added opcodes, no changed
encodings. The prelude, multi-opcode immediates, qstr indexes and `.mpy`
container are the pin's. Emit stock bytecode.

## Target constraints

These are requirements, not preferences, and they shape the architecture:

- **32 Python frames.** The call stack is a fixed pool of hardware-managed
  frames in the same 256 KB SRAM as the heap, and each frame also holds that
  function's Python slots. It cannot be raised; overflow is
  `RuntimeError: maximum recursion depth exceeded`. **Recursion is banned
  outright** — nested parsing and code generation go on an explicit stack in
  a `bytearray` or `array('i')`, which lives in the heap. Enforced statically
  by `tools/lint_frames.py`.
- **64 KB heap peak** for a 200-line module.
- **No bigint.** The widest integer is signed 64-bit. A literal inside the
  31-bit small-int range uses the small-int encoding; one that fits in 64 bits
  but not 31 goes in the const pool as an int; anything wider is a compile
  error. See `DEVIATIONS.md` — the oracle disagrees here, by design.
- **Object sizes are not the stock ones.** A stored value is a 32-bit word
  plus a 4-bit tag, eight tags packed per word, with no per-value malloc
  header. Do not budget from CPython or from the 64-bit unix port.
- **Absent string helpers.** `str.isalnum`, `str.isidentifier` and
  `str.expandtabs` do not exist at the pin. `isalpha`, `isdigit`, `isspace`,
  `isupper`, `islower` do. The lexer rule is `py/lexer.c`'s: a head character
  is a letter, `_`, or any character `>= 0x80`; a tail character is a head
  character or a digit. No `unicodedata`.

The shipped package also avoids `enum`, `typing`, `functools`, `itertools`
and `weakref`, none of which exist at the pin.

## Status

| component | state |
|---|---|
| `mpyc/mpy_format.py` — `.mpy` container codec | **done and verified** |
| `mpyc/parsetree.py` — packed parse tree | **done; memory budget cleared** |
| `mpyc/qstrs.py` + `qstrtable.py` — static qstr pool | **done and verified against the oracle** |
| `mpyc/tokens.py` + `lexer.py` — tokenizer | **done except t-strings; 2187 files token-exact** |
| `mpyc/grammar.py` — grammar tables | **done; numbering confirmed against the C enum** |
| parser engine + constant folding | in progress |
| scope analysis, emitter | not started |

**Container codec: 2083 / 2083 files byte-exact** against the pinned
`mpy-cross`, over MicroPython's own `tests/`, `lib/micropython-lib`, `tools/`
and `py/` trees. Zero mismatches, zero decode or encode errors. A further 116
files are rejected by the oracle itself (CPython-only syntax in `cpydiff`,
REPL fragments, `-march` natives) and are out of scope.

The same codec runs **under MicroPython v1.29.0**, round-tripping 60 files
byte-exact with a 26 KB peak heap — on the 64-bit unix port, where every value
is wider than on the target, so this is an upper bound.

`tools/lint_frames.py` reports the codec's deepest chain as **3 frames**
against the 32-frame budget, and is self-tested to fail on a planted cycle.

### Packed parse tree

`mpyc/parsetree.py` reproduces `py/parse.h`'s node encoding exactly — small
int, identifier, string and token leaves tagged in the low bits of one word,
struct nodes as `source_line` + `kind_num_nodes` + children — with arena
indexes substituted for C's pointers, all in one flat `array('i')`.

Measured identically on CPython and MicroPython v1.29.0:

| | |
|---|---|
| 3-child struct node | **20 bytes** (2 header + 3 child slots) — the same as the C compiler on a 32-bit target |
| a leaf | **0 extra bytes** — packed into the parent's child slot, as in C |
| capacity in 64 KB | 5461 one-child / 4096 two-child / 3276 three-child nodes |

A 5001-deep chain walks without recursion, so tree depth costs no Python
frames. The arena grows by fixed 256-slot blocks rather than doubling,
because at a 64 KB ceiling a doubling realloc needs both buffers live at
once.

**The budget scales with tokens, not lines.** Across the 44 micropython-lib
modules of 150–260 lines, the median is 1009 tokens, which at one node per
token is ~16 KB — a quarter of the budget. But `_sha256.py` is 4758 tokens in
195 lines, and at the same ratio that is ~76 KB, over budget. Token-dense
modules (large constant tables) are the risk case, not long ones. The
node-per-token ratio is a proxy until the parser exists and can be measured
directly.

### Static qstr pool

`.mpy` files refer to common strings by a fixed number instead of spelling
them out, so the numbering has to be exact. The authoritative source is
`static_qstr_list` in `py/makeqstrdata.py` — a literal Python list that
`print_qstr_data` emits after `MP_QSTRnull`, before every other qstr, which
is sorted per build. `tools/gen_qstrs.py` reads that list and cross-checks
its output against the generated headers of both the mpy-cross and unix
builds.

The pool is **166 entries, qstr 0..165**, ending at `MP_QSTR_zip`
(`QSTR_LAST_STATIC`). Both builds agree on all 166 and diverge only
afterwards, which is what makes a static index portable.

Validated differentially over 500 oracle-built `.mpy` files: **4272 static
references and 17623 inline qstrs, zero disagreements.** The check is
two-sided — every qstr the oracle wrote inline must be one our pool calls
non-static, and every index it wrote must match our pool on both text and
number.

One trap worth knowing: qstr 0 is `MP_QSTRnull`, a sentinel, and its text is
*also* the empty string. The real empty string is qstr 1, so interning `""`
must yield 1. A naive text-to-index map gets this wrong silently.

The pool holds 1110 bytes of actual text. Its heap cost depends on where the
compiler's own code lives, measured on the 64-bit unix port:

| | |
|---|---|
| importing the module fresh (strings allocated on the heap) | ~5.6 KB |
| the tuple and lookup dict alone, strings already present | ~2.7 KB |

For a compiler frozen into flash on the target the string data is in flash,
so the heap cost is the second figure, and pointers halve again on 32 bits.
Either way it is a constant, not a per-module, bite of the 64 KB budget.
Packing the texts into one string plus an offset array would cut it to
roughly 1.5 KB should the budget get tight.

### Lexer

`mpyc/lexer.py` ports `py/lexer.c` (1161 lines), keeping its shape: the token
lives in `tok_kind` / `tok_line` / `tok_column` / `vstr` and `to_next()`
advances, so there is no object per token. Three bytes of lookahead, because
the longest decision — a prefix like `rb'` — needs that much.

f-strings are rewritten *in the lexer*, as MicroPython does: `f"{x!r:>{w}}"`
becomes the token stream for `"{!r:>{}}".format((x),(w),)`, spliced into the
input and re-lexed, so the parser never learns f-strings exist. That also
means the mechanism is unaware of Python syntax and cannot handle an
expression containing `}` or `:` — a limitation to reproduce, not fix.

**Verified against the oracle over 2187 files — every token's kind, line,
column and text identical, zero mismatches.** Corpus: MicroPython's `tests/`,
`lib/micropython-lib`, `tools/` and `py/` trees.

The oracle is `tools/lexdump.c`, which links the real `py/lexer.c` out of the
pinned mpy-cross build and prints its token stream; build it with
`tools/build_lexdump.sh`. Without it a port this size has nothing to check
itself against, since MicroPython exposes no way to see its own tokens.

`tests/lexer_digest.py` reduces a whole stream to one order-sensitive digest,
so the same corpus can be run under both runtimes and compared where
subprocess is unavailable. CPython and MicroPython v1.29.0 agree:
`files=120 tokens=18565 digest=45dda7d0`.

**Not yet done:** t-string interpolation (12 corpus files), which raises
`NotImplementedFeature` rather than lexing wrongly. `\N{...}` unicode name
escapes raise, matching the C lexer.

### Grammar tables and the parse-tree oracle

`tools/gen_grammar.py` reproduces the three tables `py/parse.c` builds from
`py/grammar.h`'s macro DSL: **176 rules, 58 with a compile function,
`RULE_const_object` at 58.** The numbering is part of the data format, since a
struct node's kind *is* its rule index, and `parse.c` orders rules as every
`DEF_RULE` in file order, then `RULE_const_object`, then every `DEF_RULE_NC`.

That numbering is confirmed two ways. `tools/parsedump.c` rebuilds the enum
through the real `py/grammar.h` and reports `RULE_const_object=58`,
`RULE_maximum_number_of=176` — matching exactly. And the dumped kinds for real
code line up with their names (`x = a + 1` is `expr_stmt` over `arith_expr`;
`if`/`def`/`for`/`class` give `if_stmt`, `funcdef`, `for_stmt`, `classdef`).

`parsedump` is the oracle for the engine. Nothing in MicroPython prints a
parse tree — `mp_parse_node_print` is compiled out and the tree never reaches
a `.mpy` — but `mp_parse()` is exported and `py/parse.h` makes the node layout
public, so it links the real parser and walks the result.

### The parser folds constants

Worth stating plainly, because it expands the engine's scope: **constant
folding happens in the parser, not the compiler.** `x = 1 + 2` produces no
`arith_expr` node at all — just the value. Measured against the oracle:

| source | parse tree |
|---|---|
| `1 + 2`, `2 * 3 + 4`, `1 << 3` | folded to a small-int leaf |
| `1 / 2`, `0.5 + 0.25` | folded to a `const_object` (float) |
| `(1, 2, 3)`, `(1, (2, 3))` | folded to a `const_object` tuple |
| `(1, a)` | *not* folded — `atom_paren` over `testlist_comp` |
| `'a' * 3` | *not* folded — `term` survives |
| `1 if a else 2` | *not* folded |
| `N = const(4)` then `N + 1` | folded to `4` via the const declaration |

So `MICROPY_COMP_CONST`, `MICROPY_COMP_CONST_TUPLE` and
`MICROPY_COMP_CONST_FOLDING` are all live and have to be ported with the
engine.

**A host/target divergence to expect here:** the oracle runs on a 64-bit host,
where a parse node's small-int leaf holds up to 2^62, so `1 << 40` folds into
a *leaf*. On the 32-bit target — and in our `array('i')` arena — the same value
exceeds the 31-bit small int and must become a `const_object` instead. Both
emit the same `.mpy`, since the container records `small_int_bits = 31` either
way, but parse-tree comparison against the oracle needs to normalise integers
outside the 31-bit range rather than treat the difference as a bug.

## Running the checks

```sh
# byte-exact round-trip against the oracle (needs mpy-cross from v1.29.0)
python3 tests/check_roundtrip.py --mpy-cross /path/to/mpy-cross CORPUS...

# the frame budget, statically
python3 tools/lint_frames.py --budget 32

# the package under MicroPython itself, with a heap watermark
micropython tests/selftest_device.py *.mpy
```

## Layout

| path | contents |
|---|---|
| `mpyc/mpy_format.py` | the `.mpy` container: header, varints, qstr table, constant objects, raw-code tree. A port of `py/persistentcode.c`, iterative throughout. |
| `tests/check_roundtrip.py` | `encode(decode(x)) == x` over a corpus, against the pinned oracle |
| `tests/selftest_device.py` | the package running on MicroPython, with a heap watermark |
| `tools/lint_frames.py` | static call-graph check: no cycles, depth within budget |
