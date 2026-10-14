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
| lexer | next |
| parser, scope analysis, emitter | not started |

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
