"""Operator numbers -- GENERATED, do not edit.

Regenerate with ``tools/gen_runtime0.py <micropython-source-tree>``.

The low part of each enum is encoded directly in the bytecode, as
``MP_BC_UNARY_OP_MULTI + op``, so these values are part of the .mpy
format and not an internal detail.
"""

# Generated from mpy

# -- unary operators ----------------------------------------------
MP_UNARY_OP_POSITIVE = 0
MP_UNARY_OP_NEGATIVE = 1
MP_UNARY_OP_INVERT = 2
MP_UNARY_OP_NOT = 3
MP_UNARY_OP_BOOL = 4
MP_UNARY_OP_LEN = 5
MP_UNARY_OP_HASH = 6
MP_UNARY_OP_ABS = 7
MP_UNARY_OP_INT_MAYBE = 8
MP_UNARY_OP_FLOAT_MAYBE = 9
MP_UNARY_OP_COMPLEX_MAYBE = 10
MP_UNARY_OP_SIZEOF = 11

MP_UNARY_OP_NUM_BYTECODE = 4

UNARY_OP_NAMES = (
    'positive',
    'negative',
    'invert',
    'not',
    'bool',
    'len',
    'hash',
    'abs',
    'int_maybe',
    'float_maybe',
    'complex_maybe',
    'sizeof',
)

# -- binary operators ---------------------------------------------
MP_BINARY_OP_LESS = 0
MP_BINARY_OP_MORE = 1
MP_BINARY_OP_EQUAL = 2
MP_BINARY_OP_LESS_EQUAL = 3
MP_BINARY_OP_MORE_EQUAL = 4
MP_BINARY_OP_NOT_EQUAL = 5
MP_BINARY_OP_IN = 6
MP_BINARY_OP_IS = 7
MP_BINARY_OP_EXCEPTION_MATCH = 8
MP_BINARY_OP_INPLACE_OR = 9
MP_BINARY_OP_INPLACE_XOR = 10
MP_BINARY_OP_INPLACE_AND = 11
MP_BINARY_OP_INPLACE_LSHIFT = 12
MP_BINARY_OP_INPLACE_RSHIFT = 13
MP_BINARY_OP_INPLACE_ADD = 14
MP_BINARY_OP_INPLACE_SUBTRACT = 15
MP_BINARY_OP_INPLACE_MULTIPLY = 16
MP_BINARY_OP_INPLACE_MAT_MULTIPLY = 17
MP_BINARY_OP_INPLACE_FLOOR_DIVIDE = 18
MP_BINARY_OP_INPLACE_TRUE_DIVIDE = 19
MP_BINARY_OP_INPLACE_MODULO = 20
MP_BINARY_OP_INPLACE_POWER = 21
MP_BINARY_OP_OR = 22
MP_BINARY_OP_XOR = 23
MP_BINARY_OP_AND = 24
MP_BINARY_OP_LSHIFT = 25
MP_BINARY_OP_RSHIFT = 26
MP_BINARY_OP_ADD = 27
MP_BINARY_OP_SUBTRACT = 28
MP_BINARY_OP_MULTIPLY = 29
MP_BINARY_OP_MAT_MULTIPLY = 30
MP_BINARY_OP_FLOOR_DIVIDE = 31
MP_BINARY_OP_TRUE_DIVIDE = 32
MP_BINARY_OP_MODULO = 33
MP_BINARY_OP_POWER = 34
MP_BINARY_OP_DIVMOD = 35
MP_BINARY_OP_CONTAINS = 36
MP_BINARY_OP_REVERSE_OR = 37
MP_BINARY_OP_REVERSE_XOR = 38
MP_BINARY_OP_REVERSE_AND = 39
MP_BINARY_OP_REVERSE_LSHIFT = 40
MP_BINARY_OP_REVERSE_RSHIFT = 41
MP_BINARY_OP_REVERSE_ADD = 42
MP_BINARY_OP_REVERSE_SUBTRACT = 43
MP_BINARY_OP_REVERSE_MULTIPLY = 44
MP_BINARY_OP_REVERSE_MAT_MULTIPLY = 45
MP_BINARY_OP_REVERSE_FLOOR_DIVIDE = 46
MP_BINARY_OP_REVERSE_TRUE_DIVIDE = 47
MP_BINARY_OP_REVERSE_MODULO = 48
MP_BINARY_OP_REVERSE_POWER = 49
MP_BINARY_OP_NOT_IN = 50
MP_BINARY_OP_IS_NOT = 51

MP_BINARY_OP_NUM_BYTECODE = 35

BINARY_OP_NAMES = (
    'less',
    'more',
    'equal',
    'less_equal',
    'more_equal',
    'not_equal',
    'in',
    'is',
    'exception_match',
    'inplace_or',
    'inplace_xor',
    'inplace_and',
    'inplace_lshift',
    'inplace_rshift',
    'inplace_add',
    'inplace_subtract',
    'inplace_multiply',
    'inplace_mat_multiply',
    'inplace_floor_divide',
    'inplace_true_divide',
    'inplace_modulo',
    'inplace_power',
    'or',
    'xor',
    'and',
    'lshift',
    'rshift',
    'add',
    'subtract',
    'multiply',
    'mat_multiply',
    'floor_divide',
    'true_divide',
    'modulo',
    'power',
    'divmod',
    'contains',
    'reverse_or',
    'reverse_xor',
    'reverse_and',
    'reverse_lshift',
    'reverse_rshift',
    'reverse_add',
    'reverse_subtract',
    'reverse_multiply',
    'reverse_mat_multiply',
    'reverse_floor_divide',
    'reverse_true_divide',
    'reverse_modulo',
    'reverse_power',
    'not_in',
    'is_not',
)

# -- iterator buffer ----------------------------------------------
# How many value-stack slots GET_ITER_STACK's iterator buffer takes.
MP_OBJ_ITER_BUF_NSLOTS = 4
