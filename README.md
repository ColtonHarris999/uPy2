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
| `mpyc/parser.py` — engine + constant folding | **done except t-strings; 2172 files tree-exact** |
| `mpyc/scope.py` — scope and identifier analysis | **done** |
| `mpyc/opcodes.py` + `bytecode.py` — opcodes, prelude, instructions | **done; 462296 instructions round-trip** |
| the compiler walker (`py/compile.c`) | next — the last big piece |

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

**Correction.** An earlier note here claimed a host/target divergence on
integer size — that `1 << 40` folds to a small-int leaf on the 64-bit oracle
but must become a `const_object` on the 32-bit target. That was wrong, and it
was an artifact of a misconfigured oracle: `parsedump` did not initialise
`mp_dynamic_compiler`, which `make_node_const_object_optimised` consults.
mpy-cross's own `main()` sets `small_int_bits = 31`, so the real tool keeps an
integer as a leaf only if it fits the *target's* small int. With the oracle
fixed the boundary is exact and matches our arena:

| literal | parse node |
|---|---|
| `1073741823` (2^30−1) | small-int leaf |
| `1073741824` (2^30) | `const_object` |
| `1 << 40` | `const_object` |

There is no divergence to normalise.

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

### Parser engine

`mpyc/parser.py` ports `py/parse.c`: a table-driven LL parser over an explicit
rule stack and result stack, writing nodes into the arena. Besides parsing it
does three things that shape the tree the compiler reads — node folding
(`and_ident` rules with one non-null argument emit nothing), constant folding,
and docstring discarding (with `MICROPY_ENABLE_DOC_STRING` off, a lonely
constant statement becomes `pass`).

**Verified against the oracle over 2172 files — every node's kind, source
line, child count and every leaf's value identical, zero mismatches.** A
further 15 files are rejected by both implementations and 12 use t-strings.

Constant folding covers arithmetic, logical short-circuiting, `not`, const
tuples, `const()` declarations, and `math.pi`/`e`/`tau`/`inf`/`nan` via
`MICROPY_COMP_MODULE_CONST`. `errno` and `uctypes` are absent from mpy-cross's
build — probing the oracle leaves `errno.ENOENT` unfolded — so only `math` is
in the table.

`tests/parse_digest.py` reduces whole trees to one digest for cross-runtime
comparison. CPython and MicroPython v1.29.0 agree:
`files=100 nodes=15503 digest=fabb425b`.

### The 64 KB budget holds

Measured on MicroPython v1.29.0, parsing real micropython-lib modules:

| lines | live | arena | nodes |
|---|---|---|---|
| 151–155 | 7.2–33.7 KB | 3.6–9.9 KB | 205–649 |
| 249–267 | 7.8–19.5 KB | 6.9–13.4 KB | 378–781 |

**Live** is what survives a collect with the tree still held — the figure that
has to last across all four compiler passes. Worst case so far is 33.7 KB
against the 64 KB budget, on a 64-bit build, so a 32-bit target should be
lower still for the pointer-heavy parts.

The distinction matters: measured *without* collecting, the same parses look
like 0.3–1.8 MB, because that counts every transient allocation. That churn is
real but it is a GC-pressure and speed concern, not a budget violation. It is
the obvious next optimisation if on-device parse time disappoints — the
per-node `children` list and the constant-key tuples are the main sources.

### The qstr pool: the device's

A string literal longer than 10 bytes becomes a qstr leaf only if an equal
string **is already interned**, and a constant object otherwise — and those
compile to different bytecode. So the outcome depends on the full qstr pool of
whichever build does the compiling, not just the portable 166 static qstrs.

**This compiler uses the device's pool**, because it runs on the device: a
literal's fate must be decided by the pool its bytecode will actually run
against. `mpyc/qstr_pool.py` is generated from a firmware build by
`tools/gen_qstr_pool.py`; `QstrPool` also takes a pool as a parameter.

> The committed `mpyc/qstr_pool.py` is generated from the **unix port** (773
> entries) as a stand-in. Regenerate it from your firmware's
> `genhdr/qstrdefs.generated.h` before trusting its output on silicon.

The accepted consequence: **mpy-cross is no longer a byte-exact oracle for
every file.** The two pools are not nested — 465 entries are only in the unix
build's, 274 only in mpy-cross's — and over the 2172-file corpus **42 files
(1.9%) parse to different trees** depending on which pool is used
(`tests/basics/fun_globals.py`, `tests/extmod/framebuf1.py`, …).

So the differential tests hand the parser *mpy-cross's* pool
(`tests/oracle_qstr_pool.py`), which keeps them checking the engine rather
than the pool choice; on that footing the corpus is still 2172 exact. What
they no longer prove is that on-device output matches mpy-cross byte for byte
on those 42 files — by design, since matching the device is what was asked
for. Validating those needs the device's own compiler as the reference, which
the self-hosting bootstrap will provide.

### Scope analysis

A port of `py/scope.c`: one `Scope` per block, names kept in first-mention
order (that order sets local slot numbers, so it is load-bearing), and the
closure algorithm that threads a variable from the block reading it up to the
block owning it — the owner's `LOCAL` becomes `CELL`, and every block between
gains a `FREE` entry so the value can pass through.

Scopes never leave MicroPython's compiler, so there is no oracle for this
piece; the tests pin the algorithm's invariants instead, including the cases
where nothing should happen (a module-level name is global, not free; a name
no parent owns stays undecided; closing over twice adds no duplicate). A
400-deep closure resolves iteratively, costing no frames for depth.

### Bytecode prelude

Each function's `fun_data` begins with two bit-interleaved headers — a
six-value signature packed as `xSSSSEAA [xFSSKAED …]` and a size field as
`xIIIIIIC …` — then the qstr-indexed name section, a line-number table in two
alternating formats, and the closure section.

`mpyc/bytecode.py` encodes and decodes all of it. Decoding is what makes the
encoder testable before the compiler walker exists: every `fun_data` the
oracle produces can be taken apart and rebuilt, and the bytes must match.

**10010 functions across 2083 oracle-built `.mpy` files round-trip byte for
byte**, identically under CPython and MicroPython v1.29.0 — exercising both
header forms, multi-byte extensions, and both line-info encodings over
whatever shapes the corpus holds.

### Opcodes and instruction emission

`py/bc0.h` does not carry a per-opcode argument table: the shape comes from an
opcode's *high nibble* through a magic number,
`(0x000003a4 >> (2 * (op >> 4))) & 3`. So `tools/gen_opcodes.py` takes the
values and that magic number from the pinned header together, resolves the
`BASE + offset` arithmetic most definitions use, and refuses to emit a table
if the magic number ever changes or two opcodes collide.

**77 opcodes** — BYTE 29, QSTR 13, VAR_UINT 24, OFFSET 11 — plus the five
`*_MULTI` ranges, which encode their operand in the opcode byte and so span
many values each.

`mpyc/bytecode.py` decodes and encodes instructions: the MSB-first base-128
argument, the one signed case (`LOAD_CONST_SMALL_INT`, where the top group's
`0x40` bit must agree with the sign), one- or two-byte jump offsets with the
signed-opcode range biased by `0x40`/`0x4000`, and the trailing byte that
opcodes passing `(op & 0x9e) == 0` carry.

**Verified over the corpus: 2083 files, 10010 functions, 462296 instructions,
207 distinct opcode values, zero failures** — identically under CPython and
MicroPython v1.29.0. Two properties at once: the walk lands *exactly* on the
end of every function's bytecode, which is what validates the shape
derivation for every opcode seen; and every instruction re-encodes to its own
bytes, which validates emission.

Running it on the target paid for itself again. `disassemble` originally
returned a list, and one real corpus function was big enough that collecting
its instructions asked for 64 KB in a single allocation and raised
`MemoryError` under MicroPython. It is now a generator, and the test compares
inside the streaming loop — the kind of allocation the budget cannot spare,
caught before it reached silicon.
