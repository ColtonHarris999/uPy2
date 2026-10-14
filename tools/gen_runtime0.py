"""Generate ``mpyc/runtime0.py``: the unary and binary operator enums.

``py/runtime0.h`` declares ``mp_unary_op_t`` and ``mp_binary_op_t`` as plain C
enums, and the first part of each is *in the bytecode*: ``MP_BC_UNARY_OP_MULTI
+ op`` and ``MP_BC_BINARY_OP_MULTI + op`` turn the operator number straight
into an opcode byte.  The header says as much ("Changing this group in any way
requires changing the bytecode version"), so these numbers are part of the
format and are read from the pinned header rather than retyped.

The two counts ``MP_UNARY_OP_NUM_BYTECODE`` and ``MP_BINARY_OP_NUM_BYTECODE``
bound what may legally reach the emitter; they are derived here the way the
header derives them.

    python3 tools/gen_runtime0.py /path/to/micropython-v1.29.0
"""
import os
import re
import sys


def parse_enum(text, type_name):
    """The members of ``typedef enum { ... } <type_name>;``, in order.

    Only bare members appear in these two enums (no ``= value``), so the
    numbering is positional; the function refuses anything else rather than
    silently mis-numbering the operators.
    """
    m = re.search(r"typedef enum \{([^{}]*)\}\s*" + type_name + r"\s*;",
                  text, re.S)
    if not m:
        raise SystemExit("could not find enum %s" % type_name)
    body = m.group(1)
    # Strip comments, which carry commas and braces of their own.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    names = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        if not re.match(r"^MP_(UNARY|BINARY)_OP_\w+$", part):
            raise SystemExit("unexpected enum member %r in %s"
                             % (part, type_name))
        names.append(part)
    return names


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    tree_root = argv[0]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = argv[1] if len(argv) > 1 else os.path.join(
        here, "mpyc", "runtime0.py")
    with open(os.path.join(tree_root, "py", "runtime0.h"), "r") as f:
        text = f.read()

    unary = parse_enum(text, "mp_unary_op_t")
    binary = parse_enum(text, "mp_binary_op_t")

    # The header defines the bytecode boundaries in terms of members; resolve
    # them the same way so a reordering upstream cannot go unnoticed.
    unary_bc = unary.index("MP_UNARY_OP_NOT") + 1
    binary_bc = binary.index("MP_BINARY_OP_POWER") + 1

    # mp_obj_iter_buf_t is a base plus three slots, and MP_OBJ_ITER_BUF_NSLOTS
    # rounds its size up to whole object slots.  Since mp_obj_base_t is one
    # pointer, that is 4 on every representation -- and it must be, because
    # the compiler bakes it into stack sizes that the .mpy records.
    with open(os.path.join(tree_root, "py", "obj.h"), "r") as f:
        obj_h = f.read()
    m = re.search(r"typedef struct _mp_obj_iter_buf_t \{(.*?)\}", obj_h, re.S)
    if not m or "buf[3]" not in m.group(1):
        raise SystemExit("mp_obj_iter_buf_t is no longer base + buf[3]")
    iter_nslots = 4

    lines = []
    w = lines.append
    w('"""Operator numbers -- GENERATED, do not edit.')
    w("")
    w("Regenerate with ``tools/gen_runtime0.py <micropython-source-tree>``.")
    w("")
    w("The low part of each enum is encoded directly in the bytecode, as")
    w("``MP_BC_UNARY_OP_MULTI + op``, so these values are part of the .mpy")
    w("format and not an internal detail.")
    w('"""')
    w("")
    w("# Generated from %s" % os.path.basename(os.path.abspath(tree_root)))
    w("")
    w("# -- unary operators ----------------------------------------------")
    for i, name in enumerate(unary):
        w("%s = %d" % (name, i))
    w("")
    w("MP_UNARY_OP_NUM_BYTECODE = %d" % unary_bc)
    w("")
    w("UNARY_OP_NAMES = (")
    for name in unary:
        w("    %r," % name[len("MP_UNARY_OP_"):].lower())
    w(")")
    w("")
    w("# -- binary operators ---------------------------------------------")
    for i, name in enumerate(binary):
        w("%s = %d" % (name, i))
    w("")
    w("MP_BINARY_OP_NUM_BYTECODE = %d" % binary_bc)
    w("")
    w("BINARY_OP_NAMES = (")
    for name in binary:
        w("    %r," % name[len("MP_BINARY_OP_"):].lower())
    w(")")
    w("")
    w("# -- iterator buffer ----------------------------------------------")
    w("# How many value-stack slots GET_ITER_STACK's iterator buffer takes.")
    w("MP_OBJ_ITER_BUF_NSLOTS = %d" % iter_nslots)
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print("unary ops : %d (%d in bytecode)" % (len(unary), unary_bc))
    print("binary ops: %d (%d in bytecode)" % (len(binary), binary_bc))
    print("wrote     : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
