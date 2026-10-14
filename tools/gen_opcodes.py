"""Generate ``mpyc/opcodes.py``: the bytecode opcode table.

``py/bc0.h`` groups opcodes by the shape of their argument, and derives the
shape from the opcode's *high nibble* through a magic number rather than a
per-opcode table::

    #define MP_BC_FORMAT(op) ((0x000003a4 >> (2 * ((op) >> 4))) & 3)

so the values and the magic number must come from the pinned header together;
inventing either would misparse bytecode in ways that only show up on
particular instructions.

Most definitions are written as a base plus an offset
(``MP_BC_BASE_VINT_E + 0x04``), so this resolves that arithmetic rather than
assuming literals.

    python3 tools/gen_opcodes.py /path/to/micropython-v1.29.0
"""
import os
import re
import sys


def parse_defines(path):
    """``name -> value`` for every ``#define MP_BC_*``, resolving `base + n`."""
    values = {}
    order = []
    pat = re.compile(r"^#define\s+(MP_BC_\w+)\s+\((.+?)\)\s*(?://.*)?$")
    with open(path, "r") as f:
        for line in f:
            m = pat.match(line.rstrip())
            if not m:
                continue
            name, expr = m.group(1), m.group(2).strip()
            v = eval_expr(expr, values)
            if v is None:
                continue
            values[name] = v
            order.append(name)
    return values, order


def eval_expr(expr, values):
    """Evaluate `0x12`, `MP_BC_BASE_X`, or `MP_BC_BASE_X + 0x12`."""
    expr = expr.strip()
    m = re.match(r"^(0x[0-9a-fA-F]+|\d+)$", expr)
    if m:
        return int(expr, 0)
    m = re.match(r"^(MP_BC_\w+)$", expr)
    if m:
        return values.get(m.group(1))
    m = re.match(r"^(MP_BC_\w+)\s*\+\s*(0x[0-9a-fA-F]+|\d+)$", expr)
    if m:
        base = values.get(m.group(1))
        if base is None:
            return None
        return base + int(m.group(2), 0)
    return None


def grab(path, name):
    """One #define's raw text, for values we copy verbatim."""
    pat = re.compile(r"^#define\s+" + name + r"\s+(.*?)\s*(?://.*)?$")
    with open(path, "r") as f:
        for line in f:
            m = pat.match(line.rstrip())
            if m:
                return m.group(1).strip()
    raise SystemExit("could not find #define %s" % name)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    tree_root = argv[0]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = argv[1] if len(argv) > 1 else os.path.join(
        here, "mpyc", "opcodes.py")
    bc0 = os.path.join(tree_root, "py", "bc0.h")

    values, order = parse_defines(bc0)
    opcodes = {}
    bases = {}
    params = {}
    for name in order:
        if name.startswith("MP_BC_BASE_"):
            bases[name] = values[name]
        elif name.startswith("MP_BC_MASK_") or name == "MP_BC_FORMAT":
            continue
        elif name.startswith("MP_BC_FORMAT_"):
            continue
        elif name.endswith("_NUM") or name.endswith("_EXCESS"):
            # Range parameters of the *_MULTI opcode groups, not opcodes:
            # MP_BC_LOAD_FAST_MULTI_NUM is a count (16), not a byte value.
            params[name] = values[name]
        else:
            opcodes[name] = values[name]

    # Sanity: the magic number must still be the one we hard-code the
    # semantics of, and every opcode must fit a byte.
    magic = grab(bc0, r"MP_BC_FORMAT\(op\)")
    if "0x000003a4" not in magic:
        raise SystemExit("MP_BC_FORMAT's magic number changed: %s" % magic)
    for name in opcodes:
        if not 0 <= opcodes[name] <= 0xFF:
            raise SystemExit("%s = %#x does not fit a byte"
                             % (name, opcodes[name]))
    # Two opcodes must not share a value, or disassembly would be ambiguous.
    seen = {}
    for name in sorted(opcodes, key=lambda n: opcodes[n]):
        v = opcodes[name]
        if v in seen and not name.endswith("_MULTI"):
            # *_MULTI opcodes legitimately begin a range; a plain collision is
            # a parsing error.
            if not seen[v].endswith("_MULTI"):
                raise SystemExit("%s and %s both = %#x" % (seen[v], name, v))
        else:
            seen[v] = name

    lines = []
    w = lines.append
    w('"""Bytecode opcodes -- GENERATED, do not edit.')
    w("")
    w("Regenerate with ``tools/gen_opcodes.py <micropython-source-tree>``.")
    w("")
    w("An opcode's argument shape comes from its high nibble via the magic")
    w("number in ``py/bc0.h``, not from a per-opcode table, so FORMAT_MAGIC")
    w("below is that header's value and must be regenerated with it.")
    w('"""')
    w("")
    w("# Generated from %s" % os.path.basename(os.path.abspath(tree_root)))
    w("")
    w("# -- argument shapes ----------------------------------------------")
    w("MP_BC_FORMAT_BYTE = 0")
    w("MP_BC_FORMAT_QSTR = 1")
    w("MP_BC_FORMAT_VAR_UINT = 2")
    w("MP_BC_FORMAT_OFFSET = 3")
    w("")
    w("FORMAT_NAMES = (\"BYTE\", \"QSTR\", \"VAR_UINT\", \"OFFSET\")")
    w("")
    w("MP_BC_MASK_FORMAT = %s" % grab(bc0, "MP_BC_MASK_FORMAT"))
    w("MP_BC_MASK_EXTRA_BYTE = %s" % grab(bc0, "MP_BC_MASK_EXTRA_BYTE"))
    w("")
    w("# Nibbles, right to left: BB BB BB BB BB BO VV QU")
    w("FORMAT_MAGIC = 0x000003a4")
    w("")
    w("")
    w("def opcode_format(op):")
    w('    """The argument shape of *op*, from its high nibble."""')
    w("    return (FORMAT_MAGIC >> (2 * (op >> 4))) & 3")
    w("")
    w("")
    w("def has_extra_byte(op):")
    w('    """True if a trailing byte follows this opcode\'s argument."""')
    w("    return (op & MP_BC_MASK_EXTRA_BYTE) == 0")
    w("")
    w("")
    w("def has_signed_offset(op):")
    w('    """Offset-format opcodes in this range carry a signed offset."""')
    w("    return MP_BC_UNWIND_JUMP <= op <= MP_BC_POP_JUMP_IF_FALSE")
    w("")
    w("# -- group bases --------------------------------------------------")
    for name in sorted(bases, key=lambda n: bases[n]):
        w("%s = 0x%02x" % (name, bases[name]))
    w("")
    w("# -- *_MULTI range sizes ------------------------------------------")
    w("# These are counts, not opcodes: an opcode in a MULTI range encodes its")
    w("# operand in the opcode byte itself, so the range spans NUM values.")
    for name in sorted(params):
        w("%s = %d" % (name, params[name]))
    w("")
    w("# -- opcodes ------------------------------------------------------")
    for name in sorted(opcodes, key=lambda n: (opcodes[n], n)):
        w("%s = 0x%02x" % (name, opcodes[name]))
    w("")
    w("OPCODE_COUNT = %d" % len(opcodes))
    w("")
    w("# value -> name; None where no opcode has that value.  *_MULTI entries")
    w("# name the first of a range that encodes its operand in the opcode.")
    w("OPCODE_NAMES = (")
    for v in range(256):
        name = seen.get(v)
        w("    %s," % (repr(name) if name else "None"))
    w(")")
    w("")
    w("")
    w("def opcode_name(op):")
    w("    if 0 <= op < 256 and OPCODE_NAMES[op] is not None:")
    w("        return OPCODE_NAMES[op]")
    w('    return "MP_BC_?%#02x" % op')
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for name in opcodes:
        counts[(0x3A4 >> (2 * (opcodes[name] >> 4))) & 3] += 1
    print("opcodes   : %d (%d group bases)" % (len(opcodes), len(bases)))
    print("by shape  : BYTE=%d QSTR=%d VAR_UINT=%d OFFSET=%d"
          % (counts[0], counts[1], counts[2], counts[3]))
    print("wrote     : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
