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
| `mpyc/tokens.py` + `lexer.py` — tokenizer | **done; 2268 files token-exact** |
| `mpyc/grammar.py` — grammar tables | **done; numbering confirmed against the C enum** |
| `mpyc/parser.py` — engine + constant folding | **done; 2253 files tree-exact** |
| `mpyc/scope.py` — scope and identifier analysis | **done; identifier table packed into `array('i')`** |
| `mpyc/opcodes.py` + `bytecode.py` — opcodes, prelude, instructions | **done; 462296 instructions round-trip** |
| `mpyc/emitbc.py` — the emitter state machine | **done** |
| `mpyc/compile.py` — the compiler walker | **done; 2148 files byte-exact end to end** |

**End to end: 2148 / 2149 files compile byte-for-byte** into the same `.mpy`
`mpy-cross` v1.29.0 produces, over MicroPython's own `tests/`, `tools/`,
`examples/` and `lib/` trees. The one remaining file differs only in how a
float constant is spelled, and the two spellings read back as the same double
(DEVIATIONS.md, D2). 120 further files are rejected by the oracle itself and
are rejected here too — **nothing the oracle refuses is accepted**, and
nothing is left deferred.

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

**Verified against the oracle over 2268 files — every token's kind, line,
column and text identical, zero mismatches.** Corpus: MicroPython's `tests/`,
`lib/micropython-lib`, `tools/`, `examples/` and `py/` trees. The one
remaining file of 2269 is refused by both implementations, for the same
reason (see below).

The oracle is `tools/lexdump.c`, which links the real `py/lexer.c` out of the
pinned mpy-cross build and prints its token stream; build it with
`tools/build_lexdump.sh`. Without it a port this size has nothing to check
itself against, since MicroPython exposes no way to see its own tokens.

`tests/lexer_digest.py` reduces a whole stream to one order-sensitive digest,
so the same corpus can be run under both runtimes and compared where
subprocess is unavailable. CPython and MicroPython v1.29.0 agree:
`files=120 tokens=18565 digest=45dda7d0`.

`\N{...}` unicode name escapes raise `NotImplementedByMicroPython`, because
`py/lexer.c` raises `NotImplementedError` at the same point rather than carry
the Unicode name table; `mpy-cross` refuses such a file too. That is a
separate exception class from `NotImplementedFeature` precisely so a faithful
refusal cannot be mistaken for a gap in the port — there are no gaps left.

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
# the whole compiler, against the oracle, end to end
python3 tests/test_compile.py --mpy-cross /path/to/mpy-cross CORPUS...

# the same files compiled under both runtimes, compared by digest
python3     tests/compile_digest.py FILE.py...
micropython -X heapsize=256M tests/compile_digest.py FILE.py...

# what a compile costs on the target
micropython tests/compile_heap.py FILE.py...

# the compiler compiled by itself, to a fixed point
python3 tools/bootstrap.py --micropython PATH --mpy-cross PATH --corpus DIR

# the frame budget, statically
python3 tools/lint_frames.py --budget 32

# the stages on their own
python3 tests/test_lexer.py   --lexdump   tools/lexdump   CORPUS...
python3 tests/test_parser.py  --parsedump tools/parsedump CORPUS...
python3 tests/test_bytecode.py --mpy-cross /path/to/mpy-cross CORPUS...
python3 tests/test_parsetree.py tests/test_qstrs.py tests/test_scope.py
```

## Layout

| path | contents |
|---|---|
| `mpyc/mpy_format.py` | the `.mpy` container: header, varints, qstr table, constant objects, raw-code tree. A port of `py/persistentcode.c`, iterative throughout. |
| `tests/check_roundtrip.py` | `encode(decode(x)) == x` over a corpus, against the pinned oracle |
| `tests/selftest_device.py` | the package running on MicroPython, with a heap watermark |
| `mpyc/emitbc.py` | the emitter: the four-pass state machine, dead-code suppression, jump sizing, and one method per bytecode operation. A port of `py/emitbc.c`. |
| `mpyc/compile.py` | the walker's entry points and pass driver, and `Compiler`, the last link of the chain below. A port of `py/compile.c`. |
| `mpyc/compile_defs.py` | the walker's constants: the exception, the emitter-step table, the work-item kinds |
| `mpyc/compile_walk.py` | `Walker`: the trampoline over an explicit work stack, the dispatch tables, the steps |
| `mpyc/compile_assign.py` | `AssignWalker`: conditions, assignment, `del` |
| `mpyc/compile_defn.py` | `DefnWalker`: scopes, parameters, `def`, `lambda`, `class`, declarations |
| `mpyc/compile_stmt.py` | `StmtWalker`: simple statements, imports, `assert`, `if`, `while`, `for` |
| `mpyc/compile_block.py` | `BlockWalker`: `try`, `with`, `async`, expression statements |
| `mpyc/compile_expr.py` | `ExprWalker`: operators, trailers, calls, comprehensions, atoms |
| `tests/test_compile.py` | compile a corpus and require the `.mpy` to equal the oracle's, byte for byte |
| `tests/compile_digest.py` | one digest per corpus, for comparing the two runtimes |
| `tests/compile_heap.py` | peak live heap of a whole compile, sampled from inside |
| `mpyc/floatfmt.py` | how a float constant is spelled, with the exact arithmetic that needs. A port of the `repr` path through `py/formatfloat.c`. |
| `tests/test_floatfmt.py` | float spellings read back exactly, and the same text on either runtime |
| `tools/bootstrap.py` | compiles the compiler with itself, three stages, and checks the result is a fixed point |
| `tools/lint_frames.py` | static call-graph check: no cycles, depth within budget |

### Parser engine

`mpyc/parser.py` ports `py/parse.c`: a table-driven LL parser over an explicit
rule stack and result stack, writing nodes into the arena. Besides parsing it
does three things that shape the tree the compiler reads — node folding
(`and_ident` rules with one non-null argument emit nothing), constant folding,
and docstring discarding (with `MICROPY_ENABLE_DOC_STRING` off, a lonely
constant statement becomes `pass`).

**Verified against the oracle over 2253 files — every node's kind, source
line, child count and every leaf's value identical, zero mismatches.** The
other 16 files of 2269 are rejected by both implementations.

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
build's, 274 only in mpy-cross's — and over the corpus **42 files (1.9%)
parse to different trees** depending on which pool is used
(`tests/basics/fun_globals.py`, `tests/extmod/framebuf1.py`, …).

So the differential tests hand the parser *mpy-cross's* pool
(`tests/oracle_qstr_pool.py`), which keeps them checking the engine rather
than the pool choice; on that footing the corpus is exact. What
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

### The emitter is a state machine, not a function

`py/emitbc.c` is driven four times over the same scope, and each pass learns
something the next one needs:

| pass | what it decides |
|---|---|
| `MP_PASS_SCOPE` | which names are local, cell or free, and how many labels |
| `MP_PASS_STACK_SIZE` | the scope's maximum value-stack depth |
| `MP_PASS_CODE_SIZE` | the size of each section, and where each label lands |
| `MP_PASS_EMIT` | the bytes — repeated until the size stops changing |

Two mechanisms carry the weight, and both are easy to get subtly wrong.

**Dead code.** `suppress` drops everything written after an unconditional
jump, raise or return until the next label — that is MicroPython's whole
dead-code elimination. A suppressed write advances no offset, so the dead
bytes never exist. Code info is *not* suppressed, which is why the two
sections need separate write paths.

**Jump sizing.** A jump offset is one or two bytes, measured from the
instruction *after* the jump — so the instruction's own length is part of the
arithmetic, and the length depends on the offset. The encoding may only ever
*shrink* from one pass to the next; if it grew, a label already placed in this
pass would have been written with a stale distance. `MP_PASS_CODE_SIZE` reads
label offsets it has not assigned yet, so it has to over-estimate: C relies on
whatever `m_new` left in the array, and `mpyc` makes that explicit with a
sentinel large enough to force the wide encoding.

### The walker does not recurse

`py/compile.c` is written as mutual recursion: `compile_node` dispatches to a
per-rule function, which calls `compile_node` on each child, so the C stack
grows with the nesting depth of the *source*. On a machine whose call stack is
a fixed pool of hardware frames, with 32 budgeted for the whole compiler, that
is not available.

So the walk runs as a trampoline:

* `_work` is an explicit stack of work items in the heap — five 32-bit words
  each in an `array('i')`, so a deep queue costs words rather than objects.
* A handler never calls the walker. It inspects its node, emits what it can
  emit now, and **returns a plan**: the work items that must run after it, in
  order. The trampoline pushes the plan in reverse, so it pops in source
  order.
* Code that has to run *between* two children becomes its own work item. That
  is why there are items like `W_CALL_ARGS_END`: C can simply write the
  statement after the loop.
* Accumulators C keeps in `compiler_t` across a loop body — a call's argument
  counts, the saved break/continue labels, a function's default-argument
  counters — live on three more explicit stacks, because the begin and the
  end are now separate items.

The Python call depth is therefore constant regardless of how deeply the
source nests. `tools/lint_frames.py` reports **13 frames against the 32-frame
budget, with zero cycles**, and it now resolves the dispatch tables rather
than losing the walker's depth to an indirect call (see below).

The walker is one class, `Compiler`, but it is **spread over eight source
modules as a chain of partial classes** — `Walker`, then `AssignWalker`,
`DefnWalker`, `StmtWalker`, `BlockWalker`, `ExprWalker`, each inheriting the
one before and `Compiler` last, with the constants they share in
`compile_defs.py`. That is for the heap, not for taste: what a
compile costs is roughly linear in the module's line count, and the walker in
one piece was 2886 lines, so it was the one module of the package the compiler
could not compile within reach of the budget (see *What it costs* below). No
method is defined twice, so the order of the chain carries no meaning; it is a
chain rather than a set of mixins only because multiple inheritance is a build
option in MicroPython (`MICROPY_MULTIPLE_INHERITANCE`) and nothing here needs
it. Dispatch still resolves across the whole chain, in the linter as well as
at run time, because `_build_rule_table` looks its handlers up on the
instance.

Two recursive shapes in `compile.c` flatten into loops rather than work items,
because the nesting is a chain rather than a tree: `compile_with_stmt_helper`
(open each `with` item in turn, close them in reverse) and
`compile_scope_comp_iter` (open each nested `for` of a comprehension, close
them in reverse).

### The order things happen in is load-bearing

Three orderings turned out to matter, and each produced a byte difference
until it was fixed.

**Scope creation order decides the qstr table.** A scope's name is interned
when its first pass starts, so the order scopes are *created* in is the order
their names enter the module's qstr table. In `compile_decorated`, C compiles
the decorator expressions first — which may define scopes of their own, such
as a lambda in `@cached(provider=lambda: ...)` — and only then creates the
function's scope. Building the plan eagerly got that backwards, so creating
the scope is its own work item (`W_FUNCDEF`, `W_CLASSDEF`). Caught by
`tools/ar_util.py`, where `<lambda>` landed one slot late.

**The source file name is interned before lexing.** `mp_lexer_new_*` takes it
as a qstr, so a string literal equal to the file's own name finds it already
interned and becomes a qstr leaf rather than a constant object. Caught by
`tests/basics/sys_path.py`, which contains the literal `"sys_path.py"`.

**Identifiers are numbered in first-mention order**, and that numbering sets
local slot numbers. So a declaration that C performs *after* walking a
sub-expression has to stay after it: the walrus target in a comprehension
(`W_NAMEDEXPR`) is declared in a work item that runs once the value expression
has been walked, not when the plan is built.

### What a compile costs on the target

`tests/compile_heap.py` samples `gc.mem_free` after every pass over every
scope — the peak is in the middle of the run, with the parse tree, the scopes
and the emitted code all live at once, so it cannot be seen from outside. It
reports two numbers: **live**, what is still reachable at the peak (measured
by collecting while holding it), which is what has to fit the budget; and
**churn**, total allocation including garbage, which is transient collector
pressure.

| lines | scopes | code | live | churn |
|---|---|---|---|---|
| 14 | 1 | 75 B | 14.4 KB | 127 KB |
| 54 | 12 | 493 B | 23.9 KB | 657 KB |
| 251 | 24 | 1623 B | 65.8 KB | 1121 KB |
| 422 | 62 | 3264 B | 96.7 KB | 240 KB |
| 621 | 30 | 3313 B | 107.9 KB | 1372 KB |

Roughly a 10 KB baseline plus 0.22 KB per line, so a 200-line module is about
**55 KB live against the 64 KB budget** — on the 64-bit unix port, where every
pointer, every list slot and every dictionary entry is twice the target's
width, so this is an upper bound rather than the figure.

**t-strings cost more.** Each one expands in the lexer into several times its
own length of source, which is then re-lexed, so a t-string-dense module
carries more tokens, more nodes and more qstrs per line. Measured on
MicroPython's own `string_tstring_basic.py`, truncated to length:

| lines | live | churn |
|---|---|---|
| 86 | 52.6 KB | 2.6 MB |
| 171 | 74.8 KB | 5.4 MB |
| 340 | 137.7 KB | 9.8 MB |

Linear in both, so there is no hidden quadratic in the rewrite — it is simply
about 1.5× the per-line cost of ordinary code, on top of a larger baseline.
A 200-line module of that density would be around 80 KB on the 64-bit build.
It also means the unix port's *default* heap is not enough to hold a corpus of
such files in one process, which is why the digest tests want
`-X heapsize=...`; that is a limit of the test harness, not of a compile.

Getting there took three measured fixes, each of which is in the code as a
comment:

| what | before | after |
|---|---|---|
| the firmware qstr pool, 607 entries | 30.8 KB as a set of strings | **8.7 KB** as one sorted length-prefixed blob, searched by bisection |
| the static qstr pool, rebuilt per parse | 8.2 KB each | **0.2 KB** — built once at module level |
| a scope's identifiers, 160 of them | 42.9 KB as one object each | **24.8 KB** — four words each in an `array('i')`, found by linear scan as `scope_find` does |

### Verified end to end

| check | result |
|---|---|
| byte-exact against `mpy-cross` v1.29.0 | **2148 / 2149 files** |
| functions emitted | 10337, 466380 instructions, **207 distinct opcode values** |
| the one exception | a float constant spelled differently, same value (D2) |
| rejected by the oracle, rejected here | **120 / 120** |
| accepted here but rejected by the oracle | **0** |
| crashes | **0** |
| the same files under CPython and MicroPython v1.29.0 | **identical, all of them** |
| the compiler compiled by itself | **a fixed point**: stage 0 == mpy-cross, stage 1 == stage 0, stage 2 == stage 1 |
| frame budget | **13 of 32, zero cycles** |

A single wrong byte anywhere — in the lexer, the folder, the scope analysis,
the emitter, the qstr or constant table, or the container — fails this test.
That is what makes it worth more than the per-stage checks that came before
it: those compared a piece of the pipeline against a piece of the oracle,
this one compares the product.

### The frame linter had to learn about dispatch tables

The walker calls everything through two tables — `self._steps[op](...)` and
`self._rules[kind](pn)`, plus `self._emit_fns[i](...)` — and a call through a
subscript is invisible to a name-based call graph. The linter reported
9 frames and no cycles while *not seeing the walker at all*.

It now resolves three shapes: a table assigned a literal of `self.m`
references; a table built with `getattr` in the same function that assigns it,
narrowed to a literal name prefix (`getattr(self, "h_" + name)` resolves to
exactly the `h_*` methods); and the receiver's class from an earlier
`self.obj = SomeClass(...)`. Anything it still cannot resolve — including a
table it can see but cannot look into — is **counted and reported**, so the
gap is visible rather than silent. With that, the deepest chain is 13 frames
and runs through the walker, as it should.

Two code changes came out of fixing the tool rather than the other way round,
and both are commented as such: the trampoline indexes `self._steps` instead
of a local alias, and `_s_emit` indexes `self._emit_fns` in place of binding
it to a local first. A dispatch that the tool cannot see is a dispatch nobody
can audit.

### Spelling a float constant

A float constant is stored in an `.mpy` as *text* — `save_obj` writes its
`repr` — so the exact spelling is part of the file, and
`mpyc/floatfmt.py` has to reproduce MicroPython's. That is not CPython's: at
the pin, `repr` renders **16** significant digits and only goes further if the
result does not read back as the same double, so MicroPython prints
`8.888000000000001e+32` where CPython prints `8.888e+32`.

The first version borrowed `"%.17e"` and `float(text)` to do the digits and
the round-trip check. Both are the *runtime's* conversions, MicroPython's are
not correctly rounded, and the corpus caught it: one file compiled to
different bytes under CPython than under MicroPython. A compiler whose output
depends on its host is not a compiler, so `floatfmt.py` now does its own exact
arithmetic — a small fixed-purpose multiprecision integer over 30-bit limbs in
an `array('i')`, with four operations (multiply by a small integer, divide by
a small integer, compare, read out digits), every intermediate inside a signed
64-bit value. Exactness is not optional for a second reason: "do 16 digits
read back as this double?" is decided against a half-unit-in-the-last-place
boundary, and an approximate answer picks the wrong number of digits.

Sixteen or seventeen digits is the whole range, since seventeen always
identify an IEEE double, so the search is one exact test rather than a loop —
`formatfloat.c`'s 18 and 19 are unreachable once the rounding is correct.

What is left is a 0.12% disagreement with the oracle on *which* of two
16-digit spellings to use, and it is the oracle that comes off worse:

| | |
|---|---|
| sample doubles whose spelling matches `mpy-cross` | 4218 of 4223 |
| differ, both spellings denoting the same double | 3 |
| **oracle spellings that do not denote their own value** under a correct parser | **2** |
| spellings of ours that do not denote their own value | **0** |
| corpus files affected | 1 of 2138 |

`mpy-cross` verifies its spelling by re-reading it with MicroPython's parser,
which is inexact in the same direction as its formatter, so the two agree with
each other without the text being a faithful decimal for the double. Every
spelling this compiler emits reads back exactly — under MicroPython's parser
and under a correct one — which is the property the loader in
`py/persistentcode.c` actually depends on. DEVIATIONS.md D2 has the numbers,
including why matching the oracle's rounding is not worth wanting: a 32-bit
target compiles `MICROPY_FLOAT_FORMAT_IMPL_APPROX` rather than `..._EXACT`,
and those two were built and measured at the pin as disagreeing on **2290 of
5631 doubles**.

### t-strings

A t-string is rewritten in the lexer, as an f-string is, but into a different
shape. `py/lexer.c` turns `f"a{x}b"` into `"a{}b".format((x),)` and splices
that text back into its own input; a t-string instead becomes a call to
`__template__` whose first argument is the tuple of literal pieces and whose
remaining arguments come four at a time, one group per interpolation:

| source | rewritten to |
|---|---|
| `t"abc"` | `__template__(("abc",),)` |
| `t"a{x}b"` | `__template__(("a","b",),(x),"x",None,"",)` |
| `t"{x!r}"` | `__template__(("","",),(x),"x","r","",)` |
| `t"{x:>10}"` | `__template__(("","",),(x),"x",None,">10",)` |
| `t"{x=}"` | `__template__(("x=","",),(x),"x",'r',"",)` |
| `t"{x:>{w}}"` | `__template__(("","",),(x),"x",None,f">{w}",)` |

The four arguments are the value, the source text of the expression, the
conversion, and the format spec. The last row is the interesting one: a format
spec containing braces of its own is handed back as an *f-string*, to be
expanded when the spliced text is re-lexed — so the rewrite can recurse one
level through the lexer's own input. A t-string also differs from an f-string
in unescaping `{{` and `}}` itself rather than leaving them for `str.format`,
in passing backslash escapes through unresolved (the text is going to be
re-lexed), and in keeping its quotes rather than cutting them off.

With more than 63 interpolations the call would exceed the 255-argument limit,
so they are wrapped in one tuple instead — a case worth having in the table
below, since nothing in the corpus reaches it.

Verified at all three levels against the oracle: **token-exact, tree-exact and
byte-exact** on MicroPython's eleven `string_tstring_*` tests plus 59
hand-written cases covering adjacency, raw and triple-quoted forms, multi-line
t-strings, nested format specs, `{{`/`}}`, debug `=`, conversions, quotes
inside the expression, 70 interpolations, and nine malformed forms. The
malformed ones produce the oracle's message verbatim (`malformed f-string`,
`invalid syntax`), and nothing the oracle refuses is accepted.

`tests/test_lexer.py` run with no arguments checks those rewrites without the
oracle, and so runs on the device too. Each expectation in it was confirmed
token-exact against `py/lexer.c` on the same input first — the table records
what the C lexer does, rather than a guess at it.

### Bootstrap: the compiler compiled by itself

`tools/bootstrap.py` runs three stages over the same sources with the same
file names, so that any difference in the output is a difference in the
*compiler*:

| stage | the compiler is | compiled by |
|---|---|---|
| 0 | `mpyc/*.py`, running on the host | — |
| 1 | stage 0's `.mpy` files, running as its own bytecode | stage 0 |
| 2 | stage 1's `.mpy` files | stage 1 |

Each stage proves something the previous one cannot:

* **stage 0 == `mpy-cross`** — the compiler compiles its own source
  correctly. 25 of 25 modules byte-identical.
* **stage 1 == stage 0** — running *as the bytecode it emits* does not change
  what it emits. This is the step that would catch a bug in anything the
  compiler leans on itself: a closure, a comprehension, a default argument,
  an exception, a `try/finally`. It is now executing code it produced.
* **stage 2 == stage 1** — a fixed point. Further rounds change nothing.

```
stage 0 vs mpy-cross:              25 files, 0 differ
stage 1 vs stage 0:                25 files, 0 rejected alike, 0 differ
stage 2 vs stage 1:                25 files, 0 rejected alike, 0 differ
corpus: stage 1 vs stage 0:      1475 files, 103 rejected alike, 0 differ
```

Self-compilation only exercises what the compiler's own source happens to
use, so the corpus leg matters too: over MicroPython's `tests/` tree the
`.mpy` compiler compiles 1475 files to bytes identical to the source
compiler's, and refuses the same 103 files for the same reasons (inline
assembler, native decorators, deliberate syntax errors). *Which* files a
stage refuses is compared as well as what it emits, since a compiler that
quietly accepted more would otherwise pass.

Two things the tool checks rather than assumes, because both would turn the
test into the compiler compared against itself:

* every one of the 25 modules reports a `__file__` ending in `.mpy` under the
  expected stage directory — the import is verified *after* the first compile,
  since some of the compiler's modules are imported lazily inside
  `compile_source`;
* the stages run from `/`, so the empty entry MicroPython puts on `sys.path`
  cannot reach the checkout's own `mpyc/`. With an empty path directory the
  stage fails with `ImportError: no module named 'mpyc'`, which is the
  negative control.

```sh
python3 tools/bootstrap.py --micropython PATH --mpy-cross PATH [--corpus DIR...]
```

#### What it costs, and what that means for the device

| | |
|---|---|
| the whole compiler as `.mpy` | **119 KB**, 25 modules |
| loaded and warmed in RAM | 219 KB |
| compiling `mpyc/scope.py` (293 lines) | 57 KB peak live |
| compiling `mpyc/compile_expr.py` (552 lines) | 102 KB peak live |
| compiling `mpyc/parser.py` (928 lines) | 136 KB peak live |
| compiling `mpyc/grammar.py` (577 lines, all tables) | **182 KB peak live** |

On the 64-bit unix port, so roughly halve for the target. The cost is linear
in lines for ordinary code and worse for a module that is mostly constant
tables, which is why 577 lines of `grammar.py` cost more than 928 of
`parser.py`.

Splitting the walker removed the one real outlier: in one piece
`mpyc/compile.py` peaked at **451 KB**, fourteen times the 200-line module the
64 KB budget is written for and far out of reach of 256 KB of SRAM. Its
largest slice now peaks at 102 KB, which is the same band as the rest of the
package — `grammar.py`, `parser.py`, `emitbc.py` and `lexer.py` are the four
modules still above it, and halving for a 32-bit build puts them at roughly
91, 68, 73 and 56 KB. So the package is within a factor of 1.5 of compiling
every one of its own modules on the target, rather than a factor of seven.

None of that is a contradiction of the budget, and self-hosting on silicon
does not depend on it. The compiler is meant to ship **frozen** — 119 KB of
flash, near-zero RAM, no self-compilation at runtime — and to compile user
modules of ordinary size, which is exactly what the 64 KB figure covers. What
the bootstrap establishes is that the bytecode it emits is correct enough to
run the compiler itself, which is the strongest statement available without
the hardware. If on-device self-compilation is ever wanted, the four modules
above are what is left to split, and `grammar.py` would want its tables moved
out rather than its code.
