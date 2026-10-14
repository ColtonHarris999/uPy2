# Deviations from the oracle

The byte-exact oracle is `mpy-cross` built from MicroPython tag **v1.29.0**.
Everywhere the compiler's output differs from it, the difference is listed
here. Anything not listed is a bug.

## D1. Integer literals outside signed 64-bit

`mpy-cross` on the host is built with arbitrary-precision integers. The
target has no bigint: its widest integer is a signed 64-bit value. So the
oracle accepts literals the target must reject.

Measured at the pin:

| literal | `mpy-cross` v1.29.0 | this compiler |
|---|---|---|
| `1073741823` (2³⁰−1) | accepted, 33 B — small-int encoding | same |
| `1073741824` | accepted, 41 B — const pool | same |
| `9223372036854775807` (2⁶³−1) | accepted, 50 B — const pool | same |
| `9223372036854775808` (2⁶³) | **accepted**, 50 B | **compile error** |
| `2**200` | **accepted**, 92 B | **compile error** |

*Rule:* inside signed 64-bit, match the oracle's bytes exactly, including
the const-pool form for values that fit in 64 bits but not in the 31-bit
small int. Outside signed 64-bit, raise a compile error.

*Consequence for testing:* the harness treats "oracle accepted, we rejected"
as a **pass** for literals outside signed 64-bit. Without that, the suite
would enforce behaviour the target cannot implement.

Note the const pool stores numbers as their textual repr
(`save_obj` in `py/persistentcode.c`), so matching the oracle inside the
supported range needs no bigint of our own — only the decimal text.

## D2. How a float constant is spelled

A float constant is stored in an `.mpy` as text — `save_obj` writes its
`repr` — so the exact spelling is part of the file.

`py/objfloat.c` prints a float with `mp_print_float(..., 'g',
PF_FLAG_ALWAYS_DECIMAL, ..., MP_FLOAT_REPR_PREC)`, and in `py/formatfloat.c`
that magic precision means: render with **16** significant digits, and only if
the result does not read back as the same double, try 17, 18 and 19. So
MicroPython's `repr` is the shortest round-tripping form *at or above* 16
digits, not the shortest overall — which is why it prints
`8.888000000000001e+32` where CPython prints `8.888e+32`.
`mpyc/mpy_format.float_repr` reproduces that, including the fixed 16-digit
threshold for switching to an exponent (C's `%g` ties that threshold to the
precision, so `%g` cannot be used directly).

What it does not reproduce is the oracle's *rounding* in the few cases where
16 digits are ambiguous. `mpy-cross` scales the value by a power of ten in x86
`long double`; 10^k is not exact in 64 bits of mantissa either, so the product
can land a fraction of a unit from the correctly rounded value and the
rounding goes the other way.

Measured over 5631 doubles (a spread of awkward values plus a pseudo-random
sweep of bit patterns): **16 disagree**, all of the form "two adjacent
16-digit mantissas that both read back as the same double, and the oracle
picks the other one", plus values sitting exactly on a power of ten
(`1e+23` against `9.999999999999999e+22`). Over the 2269-file corpus
**1 file** is affected (`tests/float/string_format_modulo.py`).

*Rule:* emit the correctly rounded spelling. It always reads back as the
intended double, which is what the bytecode depends on.

*Consequence for testing:* `tests/test_compile.py` counts a file whose only
difference is a float constant as "float spelling only", but **only after
checking that the two spellings parse to the same float** and that everything
else in the file is identical. A float difference that changes the value is a
failure.

*Why not reproduce it:* pure Python has no 80-bit float, and reproducing it
would be reproducing a host artefact. A 32-bit target, where `long double`
is `double`, compiles `MICROPY_FLOAT_FORMAT_IMPL_APPROX` rather than
`..._EXACT` and does not agree with `mpy-cross` on those values either.

## D3. `OrderedDict({...})` is not special-cased

`py/compile.c` has an optimisation for a literal `OrderedDict({...})`: call
`OrderedDict()` first and fill the new mapping in place, rather than building
a dict and passing it. It is guarded by `MICROPY_COMP_CONST_LITERAL &&
MICROPY_PY_COLLECTIONS_ORDEREDDICT`, and `mpy-cross` sets
`MICROPY_PY_COLLECTIONS` to 0, so the oracle does not have it.

A firmware build at the `EXTRA_FEATURES` ROM level *does*. Since the oracle
defines byte-exactness and either form runs correctly, this compiler follows
the oracle; `COMP_ORDEREDDICT_LITERAL` in `mpyc/compile.py` is the switch.

## D4. Which of two syntax errors gets reported

The walker raises on the first syntax error it finds, where `py/compile.c`
records it and lets the walk continue, reporting the first at the end. The
error reported is the same one, and the code emitted after it is thrown away
either way. The difference is visible only for input that is invalid in two
places at once *and* where the walker now reaches the second one first; the
places where C emits code before noticing are queued behind that code (the
`W_ERROR` work item) so the order is preserved.

## Non-deviations worth recording

These look like candidates and are not.

**The `.mpy` feature byte is `0x00`, not `0x03`.** `MPY_SUB_VERSION` is 3 at
the pin, and `mpy-cross` reports itself as emitting "mpy v6.3", but
`mp_raw_code_save` writes the sub-version into byte 2 only when
`cm->has_native` is set:

```c
(cm->arch_flags != 0 ? MPY_FEATURE_ARCH_FLAGS : 0) | (cm->has_native ?
    MPY_FEATURE_ENCODE_SUB_VERSION(MPY_SUB_VERSION) | MPY_FEATURE_ENCODE_ARCH(...) : 0),
```

A bytecode-only compile leaves `has_native` false, so the byte is `0x00` and
the full header is **`4d 06 00 1f`**. The loader checks the sub-version only
when the architecture nibble is not "none", so `0x00` and `0x03` both load —
but `0x00` is what the oracle writes, and byte-exactness is defined by the
oracle. Emitting `0x03` would miss on byte 3 of every file.

**Label numbers differ; label offsets do not.** A handler in the walker
allocates all of its labels before its children run, where `compile.c`
allocates them as it recurses, so the same label gets a different number.
A label is only an index into the offset table — the emitted bytes depend on
the offsets, not the numbers — and each handler allocates the same *count*,
so `max_num_labels` is unchanged. The 2269-file corpus is the evidence.

**`MP_SCOPE_FLAG_REFGLOBALS` and `MP_SCOPE_FLAG_HASCONSTS` are not set.**
`mpy-cross` sets them because it has the native emitter compiled in. Both sit
above `MP_SCOPE_FLAG_ALL_SIG` (`0x0f`), which is the mask the prelude
signature uses, so neither reaches the prelude or the `.mpy`. For the same
reason `reserve_labels_for_native` is not applied: it only inflates the label
count.

**The 32-bit register and the two-register wide value are invisible here.**
`n_state` counts Python values exactly as `mpy-cross` counts them; the target
maps a wide value onto two registers when the call starts. Emit stock
bytecode and ignore the register file.
