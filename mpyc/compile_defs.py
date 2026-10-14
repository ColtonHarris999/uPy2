"""Constants the walker's slices share.

``mpyc/compile.py`` is a chain of partial classes, one per source module, so
that no single module is large enough to blow the heap budget when the
compiler compiles itself.  Everything that is not a method of that chain --
the exception, the emitter-step table, the work-item kinds and the deferred
messages -- lives here, where every slice can import it.
"""

INVALID_LABEL = 0xFFFF

# py/smallint.h, via mp_dynamic_compiler.small_int_bits.  The star-args bitmap
# is a small int, so this also bounds how many arguments a call may have, and
# it is the value written into the .mpy header.
MP_SMALL_INT_BITS = 31
SMALL_INT_BITS = MP_SMALL_INT_BITS

# mpy-cross leaves MICROPY_PY_COLLECTIONS at 0, so
# MICROPY_PY_COLLECTIONS_ORDEREDDICT is off and the "OrderedDict({...})"
# special case in compile_atom_expr_normal is not compiled in.  A firmware
# build at the EXTRA_FEATURES ROM level *does* have it, and would emit
# different (though equivalent) code for that one expression; the oracle wins,
# as it does everywhere bytecode is concerned.
COMP_ORDEREDDICT_LITERAL = False

# assign_kind_t
ASSIGN_STORE = 0
ASSIGN_AUG_LOAD = 1
ASSIGN_AUG_STORE = 2


class CompileError(Exception):
    """A syntax error found while walking, carrying its line.

    ``Exception.__init__`` is not callable as an unbound method on
    MicroPython, so the message is kept here and rendered by ``__str__``.
    """

    def __init__(self, msg, line):
        self.msg = msg
        self.line = line

    def __str__(self):
        return "line %d: %s" % (self.line, self.msg)


# -- emitter steps --------------------------------------------------------
#
# Work items that only call one emitter method go through W_EMIT with an index
# into this table, rather than each having a work-item kind of its own.  The
# arity says how many of the item's three argument words to pass on.

_EMIT_METHODS = (
    ("pop_top", 0),
    ("dup_top", 0),
    ("dup_top_two", 0),
    ("rot_two", 0),
    ("rot_three", 0),
    ("load_null", 0),
    ("return_value", 0),
    ("end_finally", 0),
    ("start_except_handler", 0),
    ("end_except_handler", 0),
    ("for_iter_end", 0),
    ("store_map", 0),
    ("load_build_class", 0),
    ("load_const_tok", 1),
    ("load_const_small_int", 1),
    ("load_const_str", 1),
    ("unary_op", 1),
    ("binary_op", 1),
    ("label_assign", 1),
    ("jump", 1),
    ("for_iter", 1),
    ("adjust_stack_size", 1),
    ("set_source_line", 1),
    ("raise_varargs", 1),
    ("yield_", 1),
    ("get_iter", 1),
    ("unpack_sequence", 1),
    ("subscr", 1),
    ("with_cleanup", 1),
    ("attr", 2),
    ("build", 2),
    ("pop_jump_if", 2),
    ("jump_if_or_pop", 2),
    ("unwind_jump", 2),
    ("pop_except_jump", 2),
    ("unpack_ex", 2),
    ("store_comp", 2),
    ("load_method", 2),
    ("import_", 2),
    ("load_local", 3),
    ("call_function", 3),
    ("call_method", 3),
    ("async_with_setup_finally", 3),
    ("load_global", 2),
)

MI_POP_TOP = 0
MI_DUP_TOP = 1
MI_DUP_TOP_TWO = 2
MI_ROT_TWO = 3
MI_ROT_THREE = 4
MI_LOAD_NULL = 5
MI_RETURN_VALUE = 6
MI_END_FINALLY = 7
MI_START_EXCEPT_HANDLER = 8
MI_END_EXCEPT_HANDLER = 9
MI_FOR_ITER_END = 10
MI_STORE_MAP = 11
MI_LOAD_BUILD_CLASS = 12
MI_LOAD_CONST_TOK = 13
MI_LOAD_CONST_SMALL_INT = 14
MI_LOAD_CONST_STR = 15
MI_UNARY_OP = 16
MI_BINARY_OP = 17
MI_LABEL_ASSIGN = 18
MI_JUMP = 19
MI_FOR_ITER = 20
MI_ADJUST_STACK_SIZE = 21
MI_SET_SOURCE_LINE = 22
MI_RAISE_VARARGS = 23
MI_YIELD_ = 24
MI_GET_ITER = 25
MI_UNPACK_SEQUENCE = 26
MI_SUBSCR = 27
MI_WITH_CLEANUP = 28
MI_ATTR = 29
MI_BUILD = 30
MI_POP_JUMP_IF = 31
MI_JUMP_IF_OR_POP = 32
MI_UNWIND_JUMP = 33
MI_POP_EXCEPT_JUMP = 34
MI_UNPACK_EX = 35
MI_STORE_COMP = 36
MI_LOAD_METHOD = 37
MI_IMPORT_ = 38
MI_LOAD_LOCAL = 39
MI_CALL_FUNCTION = 40
MI_CALL_METHOD = 41
MI_ASYNC_WITH_SETUP_FINALLY = 42
MI_LOAD_GLOBAL = 43

for _i in range(len(_EMIT_METHODS)):
    _n = _EMIT_METHODS[_i][0]
    if globals()["MI_" + _n.upper()] != _i:
        raise AssertionError("MI_%s is not %d" % (_n.upper(), _i))

# -- work items -----------------------------------------------------------

W_EMIT = 0                # (method index, x, y, z)
W_NODE = 1                # (pn) -- compile_node
W_IF_COND = 2             # (pn, jump_if, label) -- c_if_cond
W_ASSIGN = 3              # (pn, assign_kind) -- c_assign
W_DEL = 4                 # (pn) -- c_del_stmt
W_LOAD_ID = 5             # (qst)
W_STORE_ID = 6            # (qst)
W_DELETE_ID = 7           # (qst)
W_LOAD_CONST_OBJ = 8      # (const table index)
W_CLOSE_OVER = 9          # (scope id, n_pos_defaults, n_kw_defaults)
W_SETUP_BLOCK = 10        # (label, kind) -- compile_increase_except_level
W_END_BLOCK = 11          # () -- compile_decrease_except_level
W_PARAMS_BEGIN = 12       # (scope id, params pn, list rule)
W_PARAM = 13              # (pn, list rule)
W_PARAMS_END = 14         # (scope id)
W_CALL_ARGS_BEGIN = 15    # (arglist pn, is_method, n_positional_extra)
W_CALL_ARG = 16           # (pn, index)
W_CALL_ARGS_END = 17      # (is_method)
W_LOOP_BEGIN = 18         # (break label, continue label, from_for)
W_LOOP_END = 19           # ()
W_BREAK_CONT = 20         # (is_break, pn)
W_COMPREHENSION = 21      # (pn, scope kind)
W_NAMEDEXPR = 22          # (name pn, expr pn)
W_ERROR = 23              # (pn, message index)
W_SET_GENERATOR = 24      # (funcdef pn)
W_LAMBDA_RETURN = 25      # (scope id)
W_CLASS_RETURN = 26       # (scope id)
W_FUNCDEF = 27            # (funcdef pn, emit options)
W_CLASSDEF = 28           # (classdef pn, emit options)

_WORK_STRIDE = 5

# Messages used by W_ERROR, which defers an error until the code C would have
# emitted before noticing it has been emitted.
MSG_CANNOT_ASSIGN = 0
MSG_CANNOT_DELETE = 1

_MESSAGES = (
    "can't assign to expression",
    "can't delete expression",
)
