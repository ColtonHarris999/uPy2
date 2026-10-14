"""Bytecode opcodes -- GENERATED, do not edit.

Regenerate with ``tools/gen_opcodes.py <micropython-source-tree>``.

An opcode's argument shape comes from its high nibble via the magic
number in ``py/bc0.h``, not from a per-opcode table, so FORMAT_MAGIC
below is that header's value and must be regenerated with it.
"""

# Generated from mpy

# -- argument shapes ----------------------------------------------
MP_BC_FORMAT_BYTE = 0
MP_BC_FORMAT_QSTR = 1
MP_BC_FORMAT_VAR_UINT = 2
MP_BC_FORMAT_OFFSET = 3

FORMAT_NAMES = ("BYTE", "QSTR", "VAR_UINT", "OFFSET")

MP_BC_MASK_FORMAT = (0xf0)
MP_BC_MASK_EXTRA_BYTE = (0x9e)

# Nibbles, right to left: BB BB BB BB BB BO VV QU
FORMAT_MAGIC = 0x000003a4


def opcode_format(op):
    """The argument shape of *op*, from its high nibble."""
    return (FORMAT_MAGIC >> (2 * (op >> 4))) & 3


def has_extra_byte(op):
    """True if a trailing byte follows this opcode's argument."""
    return (op & MP_BC_MASK_EXTRA_BYTE) == 0


def has_signed_offset(op):
    """Offset-format opcodes in this range carry a signed offset."""
    return MP_BC_UNWIND_JUMP <= op <= MP_BC_POP_JUMP_IF_FALSE

# -- group bases --------------------------------------------------
MP_BC_BASE_RESERVED = 0x00
MP_BC_BASE_QSTR_O = 0x10
MP_BC_BASE_VINT_E = 0x20
MP_BC_BASE_VINT_O = 0x30
MP_BC_BASE_JUMP_E = 0x40
MP_BC_BASE_BYTE_O = 0x50
MP_BC_BASE_BYTE_E = 0x60

# -- *_MULTI range sizes ------------------------------------------
# These are counts, not opcodes: an opcode in a MULTI range encodes its
# operand in the opcode byte itself, so the range spans NUM values.
MP_BC_LOAD_CONST_SMALL_INT_MULTI_EXCESS = 16
MP_BC_LOAD_CONST_SMALL_INT_MULTI_NUM = 64
MP_BC_LOAD_FAST_MULTI_NUM = 16
MP_BC_STORE_FAST_MULTI_NUM = 16

# -- opcodes ------------------------------------------------------
MP_BC_LOAD_CONST_STRING = 0x10
MP_BC_LOAD_NAME = 0x11
MP_BC_LOAD_GLOBAL = 0x12
MP_BC_LOAD_ATTR = 0x13
MP_BC_LOAD_METHOD = 0x14
MP_BC_LOAD_SUPER_METHOD = 0x15
MP_BC_STORE_NAME = 0x16
MP_BC_STORE_GLOBAL = 0x17
MP_BC_STORE_ATTR = 0x18
MP_BC_DELETE_NAME = 0x19
MP_BC_DELETE_GLOBAL = 0x1a
MP_BC_IMPORT_NAME = 0x1b
MP_BC_IMPORT_FROM = 0x1c
MP_BC_MAKE_CLOSURE = 0x20
MP_BC_MAKE_CLOSURE_DEFARGS = 0x21
MP_BC_LOAD_CONST_SMALL_INT = 0x22
MP_BC_LOAD_CONST_OBJ = 0x23
MP_BC_LOAD_FAST_N = 0x24
MP_BC_LOAD_DEREF = 0x25
MP_BC_STORE_FAST_N = 0x26
MP_BC_STORE_DEREF = 0x27
MP_BC_DELETE_FAST = 0x28
MP_BC_DELETE_DEREF = 0x29
MP_BC_BUILD_TUPLE = 0x2a
MP_BC_BUILD_LIST = 0x2b
MP_BC_BUILD_MAP = 0x2c
MP_BC_BUILD_SET = 0x2d
MP_BC_BUILD_SLICE = 0x2e
MP_BC_STORE_COMP = 0x2f
MP_BC_UNPACK_SEQUENCE = 0x30
MP_BC_UNPACK_EX = 0x31
MP_BC_MAKE_FUNCTION = 0x32
MP_BC_MAKE_FUNCTION_DEFARGS = 0x33
MP_BC_CALL_FUNCTION = 0x34
MP_BC_CALL_FUNCTION_VAR_KW = 0x35
MP_BC_CALL_METHOD = 0x36
MP_BC_CALL_METHOD_VAR_KW = 0x37
MP_BC_UNWIND_JUMP = 0x40
MP_BC_JUMP = 0x42
MP_BC_POP_JUMP_IF_TRUE = 0x43
MP_BC_POP_JUMP_IF_FALSE = 0x44
MP_BC_JUMP_IF_TRUE_OR_POP = 0x45
MP_BC_JUMP_IF_FALSE_OR_POP = 0x46
MP_BC_SETUP_WITH = 0x47
MP_BC_SETUP_EXCEPT = 0x48
MP_BC_SETUP_FINALLY = 0x49
MP_BC_POP_EXCEPT_JUMP = 0x4a
MP_BC_FOR_ITER = 0x4b
MP_BC_LOAD_CONST_FALSE = 0x50
MP_BC_LOAD_CONST_NONE = 0x51
MP_BC_LOAD_CONST_TRUE = 0x52
MP_BC_LOAD_NULL = 0x53
MP_BC_LOAD_BUILD_CLASS = 0x54
MP_BC_LOAD_SUBSCR = 0x55
MP_BC_STORE_SUBSCR = 0x56
MP_BC_DUP_TOP = 0x57
MP_BC_DUP_TOP_TWO = 0x58
MP_BC_POP_TOP = 0x59
MP_BC_ROT_TWO = 0x5a
MP_BC_ROT_THREE = 0x5b
MP_BC_WITH_CLEANUP = 0x5c
MP_BC_END_FINALLY = 0x5d
MP_BC_GET_ITER = 0x5e
MP_BC_GET_ITER_STACK = 0x5f
MP_BC_STORE_MAP = 0x62
MP_BC_RETURN_VALUE = 0x63
MP_BC_RAISE_LAST = 0x64
MP_BC_RAISE_OBJ = 0x65
MP_BC_RAISE_FROM = 0x66
MP_BC_YIELD_VALUE = 0x67
MP_BC_YIELD_FROM = 0x68
MP_BC_IMPORT_STAR = 0x69
MP_BC_LOAD_CONST_SMALL_INT_MULTI = 0x70
MP_BC_LOAD_FAST_MULTI = 0xb0
MP_BC_STORE_FAST_MULTI = 0xc0
MP_BC_UNARY_OP_MULTI = 0xd0
MP_BC_BINARY_OP_MULTI = 0xd7

OPCODE_COUNT = 77

# value -> name; None where no opcode has that value.  *_MULTI entries
# name the first of a range that encodes its operand in the opcode.
OPCODE_NAMES = (
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_LOAD_CONST_STRING',
    'MP_BC_LOAD_NAME',
    'MP_BC_LOAD_GLOBAL',
    'MP_BC_LOAD_ATTR',
    'MP_BC_LOAD_METHOD',
    'MP_BC_LOAD_SUPER_METHOD',
    'MP_BC_STORE_NAME',
    'MP_BC_STORE_GLOBAL',
    'MP_BC_STORE_ATTR',
    'MP_BC_DELETE_NAME',
    'MP_BC_DELETE_GLOBAL',
    'MP_BC_IMPORT_NAME',
    'MP_BC_IMPORT_FROM',
    None,
    None,
    None,
    'MP_BC_MAKE_CLOSURE',
    'MP_BC_MAKE_CLOSURE_DEFARGS',
    'MP_BC_LOAD_CONST_SMALL_INT',
    'MP_BC_LOAD_CONST_OBJ',
    'MP_BC_LOAD_FAST_N',
    'MP_BC_LOAD_DEREF',
    'MP_BC_STORE_FAST_N',
    'MP_BC_STORE_DEREF',
    'MP_BC_DELETE_FAST',
    'MP_BC_DELETE_DEREF',
    'MP_BC_BUILD_TUPLE',
    'MP_BC_BUILD_LIST',
    'MP_BC_BUILD_MAP',
    'MP_BC_BUILD_SET',
    'MP_BC_BUILD_SLICE',
    'MP_BC_STORE_COMP',
    'MP_BC_UNPACK_SEQUENCE',
    'MP_BC_UNPACK_EX',
    'MP_BC_MAKE_FUNCTION',
    'MP_BC_MAKE_FUNCTION_DEFARGS',
    'MP_BC_CALL_FUNCTION',
    'MP_BC_CALL_FUNCTION_VAR_KW',
    'MP_BC_CALL_METHOD',
    'MP_BC_CALL_METHOD_VAR_KW',
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_UNWIND_JUMP',
    None,
    'MP_BC_JUMP',
    'MP_BC_POP_JUMP_IF_TRUE',
    'MP_BC_POP_JUMP_IF_FALSE',
    'MP_BC_JUMP_IF_TRUE_OR_POP',
    'MP_BC_JUMP_IF_FALSE_OR_POP',
    'MP_BC_SETUP_WITH',
    'MP_BC_SETUP_EXCEPT',
    'MP_BC_SETUP_FINALLY',
    'MP_BC_POP_EXCEPT_JUMP',
    'MP_BC_FOR_ITER',
    None,
    None,
    None,
    None,
    'MP_BC_LOAD_CONST_FALSE',
    'MP_BC_LOAD_CONST_NONE',
    'MP_BC_LOAD_CONST_TRUE',
    'MP_BC_LOAD_NULL',
    'MP_BC_LOAD_BUILD_CLASS',
    'MP_BC_LOAD_SUBSCR',
    'MP_BC_STORE_SUBSCR',
    'MP_BC_DUP_TOP',
    'MP_BC_DUP_TOP_TWO',
    'MP_BC_POP_TOP',
    'MP_BC_ROT_TWO',
    'MP_BC_ROT_THREE',
    'MP_BC_WITH_CLEANUP',
    'MP_BC_END_FINALLY',
    'MP_BC_GET_ITER',
    'MP_BC_GET_ITER_STACK',
    None,
    None,
    'MP_BC_STORE_MAP',
    'MP_BC_RETURN_VALUE',
    'MP_BC_RAISE_LAST',
    'MP_BC_RAISE_OBJ',
    'MP_BC_RAISE_FROM',
    'MP_BC_YIELD_VALUE',
    'MP_BC_YIELD_FROM',
    'MP_BC_IMPORT_STAR',
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_LOAD_CONST_SMALL_INT_MULTI',
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_LOAD_FAST_MULTI',
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_STORE_FAST_MULTI',
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_UNARY_OP_MULTI',
    None,
    None,
    None,
    None,
    None,
    None,
    'MP_BC_BINARY_OP_MULTI',
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
)


def opcode_name(op):
    if 0 <= op < 256 and OPCODE_NAMES[op] is not None:
        return OPCODE_NAMES[op]
    return "MP_BC_?%#02x" % op
