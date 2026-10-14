"""The compiler walker: a port of ``py/compile.c``.

This is the half that turns a parse tree into instructions.  It walks the tree
four times -- once to work out what every name means, then once each to size
the stack, size the code and write it -- and calls ``mpyc.emitbc`` to do the
writing.

**It does not recurse.**  ``py/compile.c`` is written as mutual recursion:
``compile_node`` dispatches to a per-rule function, which calls
``compile_node`` on each child, so the C stack grows with the nesting depth of
the source.  On a machine whose call stack is a fixed pool of hardware frames,
with 32 of them budgeted for the whole compiler, that is not available.  So
the walk runs as a trampoline instead:

* ``_work`` is an explicit stack of work items in the heap, five 32-bit words
  each in an ``array('i')``.  Depth follows the *statement count*, not the
  nesting depth -- a module of 200 statements queues 200 items at once -- so
  the items are words rather than objects.
* A handler never calls the walker.  It inspects its node, emits whatever it
  can emit now, and *returns a plan*: the list of work items that must run
  after it, in order.  The trampoline pushes the plan in reverse, so it pops
  in source order.
* Code that has to run between two children becomes its own work item, which
  is why there are items like ``W_CALL_ARGS_END``: C can simply write the
  statement after the loop.

The Python call depth is therefore constant -- trampoline, dispatch, handler,
emitter -- regardless of how deeply the source nests.  ``tools/lint_frames.py``
checks that no cycle sneaks back in.

Accumulators that C keeps in ``compiler_t`` across a loop body live on three
more explicit stacks, for the same reason -- the begin and the end are now
separate work items: ``_state`` for a call's argument counts, ``_loops`` for
the saved break/continue labels, and ``_param_state`` for a function's
default-argument counters (a default value may itself be a lambda, whose own
parameters would otherwise clobber them).

Three orderings are load-bearing, and each produced a byte difference until it
was fixed.  A scope's name is interned when its first pass starts, so the
order scopes are *created* in is the order their names enter the module's qstr
table: a decorator's arguments may define scopes of their own, so creating a
function's or class's scope is a work item (``W_FUNCDEF``, ``W_CLASSDEF``)
rather than something the plan does eagerly.  Identifiers are numbered in
first-mention order, which sets local slot numbers, so a declaration C makes
*after* walking a sub-expression stays after it (``W_NAMEDEXPR``).  And the
source file name is interned before lexing, which ``mpyc/parser.py`` does.

Deliberate differences from ``compile.c``, all of them unobservable in the
bytecode:

* A syntax error raises at once rather than being recorded and letting the
  walk continue.  C reports the first error and ignores later ones, so the
  error reported is the same; only the dead code emitted after it differs,
  and that code is thrown away.
* Labels are allocated in a different order, because a handler now allocates
  all of its labels before its children run.  A label is only an index into
  the offset table, so the emitted bytes are unchanged; only the total count
  (which sizes that table) is the same by construction.
* ``MICROPY_EMIT_NATIVE``'s ``MP_SCOPE_FLAG_REFGLOBALS`` and
  ``MP_SCOPE_FLAG_HASCONSTS`` are not set.  They sit above
  ``MP_SCOPE_FLAG_ALL_SIG``, so they never reach the prelude or the ``.mpy``.
* ``reserve_labels_for_native`` is not applied, for the same reason: it only
  inflates the label count.
"""

from array import array

from . import constnode as CN
from . import emitbc as E
from . import grammar as G
from . import mpy_format as MPY
from . import parsetree as PT
from . import runtime0 as R
from . import scope as S
from . import tokens as T
from .emitbc import Emit, EmitCommon

# py/compile.c
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


class Compiler:
    """One module's walk.  Holds the scopes, the emitter and the work stack."""

    def __init__(self, tree, source_file, is_repl=False, optimise_value=0):
        self.tree = tree
        self.pool = tree.pool
        self.is_repl = is_repl
        self.optimise_value = optimise_value
        self.emit_common = EmitCommon(self.pool, self.pool.intern(source_file))
        self.emit = Emit(self.emit_common)
        self.emit.optimise_value = optimise_value

        self.pass_ = E.MP_PASS_SCOPE
        self.scopes = []
        self.scope_cur = None
        # The scope a funcdef/classdef/lambda/comprehension node owns, found
        # again on each pass.  C overwrites a spare slot of the parse node;
        # keeping it here leaves the tree alone.
        self.node_scope = {}

        self.next_label = 0
        self.break_label = INVALID_LABEL
        self.continue_label = INVALID_LABEL
        self.break_continue_except_level = 0
        self.cur_except_level = 0

        self.have_star = False
        self.num_dict_params = 0
        self.num_default_params = 0

        # The three explicit stacks: work items, saved loop labels, and the
        # argument counts of a call in progress.
        self._work = array('i')
        self._work_used = 0
        self._loops = []
        self._state = []
        self._param_state = []

        self._emit_fns = tuple(getattr(self.emit, n) for n, _a in
                               _EMIT_METHODS)
        self._emit_arity = tuple(a for _n, a in _EMIT_METHODS)
        self._steps = (
            self._s_emit, self._s_node, self._s_if_cond, self._s_assign,
            self._s_del, self._s_load_id, self._s_store_id,
            self._s_delete_id, self._s_load_const_obj, self._s_close_over,
            self._s_setup_block, self._s_end_block, self._s_params_begin,
            self._s_param, self._s_params_end, self._s_call_args_begin,
            self._s_call_arg, self._s_call_args_end, self._s_loop_begin,
            self._s_loop_end, self._s_break_cont, self._s_comprehension,
            self._s_namedexpr, self._s_error, self._s_set_generator,
            self._s_lambda_return, self._s_class_return,
            self._s_funcdef, self._s_classdef,
        )
        self._build_rule_table()

    def _build_rule_table(self):
        """One handler per rule that has a compile function, by rule number.

        The names come from ``grammar.py``, which generates them from the
        ``DEF_RULE`` entries, so a rule whose handler is missing fails here
        rather than being walked by the wrong one.

        The table is assigned here, in the same function as the ``getattr``
        that fills it, so ``tools/lint_frames.py`` can tell which methods the
        walker's dispatch reaches.
        """
        out = []
        for name in G.RULE_COMPILE_FN:
            fn = getattr(self, "h_" + name, None)
            if fn is None:
                raise AssertionError("no handler h_%s" % name)
            out.append(fn)
        self._rules = tuple(out)

    # -- the work stack --------------------------------------------------

    def _push(self, op, a, b, c, d):
        w = self._work
        used = self._work_used
        if used == len(w):
            # Grow a block at a time; array has no reserve.
            for _ in range(64 * _WORK_STRIDE):
                w.append(0)
        w[used] = op
        w[used + 1] = a
        w[used + 2] = b
        w[used + 3] = c
        w[used + 4] = d
        self._work_used = used + _WORK_STRIDE

    def plan(self, items):
        """Queue *items* so they run in the order given."""
        i = len(items) - 1
        while i >= 0:
            it = items[i]
            n = len(it)
            self._push(it[0],
                       it[1] if n > 1 else 0,
                       it[2] if n > 2 else 0,
                       it[3] if n > 3 else 0,
                       it[4] if n > 4 else 0)
            i -= 1

    def run(self):
        """Drain the work stack.

        The dispatch reads ``self._steps`` rather than a local alias so that
        ``tools/lint_frames.py`` can see which methods this reaches: this one
        call is where every step in the walk is made from, so hiding it from
        the call graph would hide the walker's whole depth.
        """
        w = self._work
        while self._work_used:
            used = self._work_used - _WORK_STRIDE
            self._work_used = used
            self._steps[w[used]](w[used + 1], w[used + 2], w[used + 3],
                                 w[used + 4])

    # -- errors ----------------------------------------------------------

    def syntax_error(self, pn, msg):
        line = 0
        if PT.is_struct(pn):
            line = self.tree.source_line(pn)
        if line == 0:
            line = self.emit.last_source_line
        raise CompileError(msg, line)

    # -- parse-node helpers ----------------------------------------------

    def _kind(self, pn):
        """The rule number of a struct node, or -1 for anything else."""
        if PT.is_struct(pn):
            return self.tree.struct_kind(pn)
        return -1

    def _is_kind(self, pn, kind):
        return PT.is_struct(pn) and self.tree.struct_kind(pn) == kind

    def _extract_list(self, pn, list_kind):
        """mp_parse_node_extract_list, as a list of node references."""
        if PT.is_null(pn):
            return []
        if PT.is_leaf(pn):
            return [pn]
        tree = self.tree
        if tree.struct_kind(pn) != list_kind:
            return [pn]
        n = tree.num_children(pn)
        out = []
        for i in range(n):
            out.append(tree.child(pn, i))
        return out

    def _is_const_true(self, pn):
        return CN.is_const_bool(self.tree, self.pool, pn, True)

    def _is_const_false(self, pn):
        return CN.is_const_bool(self.tree, self.pool, pn, False)

    def _testlist_comp_has_comp_for(self, pn):
        """MP_PARSE_NODE_TESTLIST_COMP_HAS_COMP_FOR."""
        return (self.tree.num_children(pn) == 2
                and self._is_kind(self.tree.child(pn, 1), G.RULE_comp_for))

    def next_label_(self):
        label = self.next_label
        self.next_label = label + 1
        return label

    # -- identifiers -----------------------------------------------------

    def _get_id_for_modification(self, scope, qst):
        """mp_emit_common_get_id_for_modification.  Returns the id's index."""
        i = scope.find_or_add_id(qst, S.ID_INFO_KIND_GLOBAL_IMPLICIT)
        if scope.id_kind(i) == S.ID_INFO_KIND_GLOBAL_IMPLICIT:
            if S.scope_is_func_like(scope.kind):
                scope.set_id_kind(i, S.ID_INFO_KIND_LOCAL)
            else:
                # Mark it assigned, so it cannot be closed over.
                scope.set_id_kind(i, S.ID_INFO_KIND_GLOBAL_IMPLICIT_ASSIGNED)
        return i

    def _id_op(self, which, qst):
        """mp_emit_common_id_op: pick the opcode from the name's kind.

        *which* is 0 for load, 1 for store, 2 for delete.
        """
        scope = self.scope_cur
        i = scope.find(qst)
        if i < 0:
            raise AssertionError("id q%d was not declared in this scope" % qst)
        emit = self.emit
        kind = scope.id_kind(i)
        if kind == S.ID_INFO_KIND_LOCAL:
            local, deref = True, E.MP_EMIT_IDOP_LOCAL_FAST
        elif kind in (S.ID_INFO_KIND_CELL, S.ID_INFO_KIND_FREE):
            local, deref = True, E.MP_EMIT_IDOP_LOCAL_DEREF
        elif kind == S.ID_INFO_KIND_GLOBAL_EXPLICIT:
            local, deref = False, E.MP_EMIT_IDOP_GLOBAL_GLOBAL
        else:
            local, deref = False, E.MP_EMIT_IDOP_GLOBAL_NAME
        if local:
            local_num = scope.id_local_num(i)
            if which == 0:
                emit.load_local(qst, local_num, deref)
            elif which == 1:
                emit.store_local(qst, local_num, deref)
            else:
                emit.delete_local(qst, local_num, deref)
        else:
            if which == 0:
                emit.load_global(qst, deref)
            elif which == 1:
                emit.store_global(qst, deref)
            else:
                emit.delete_global(qst, deref)

    # -- steps -----------------------------------------------------------

    def _s_emit(self, a, b, c, d):
        # Indexed in place rather than through a local, so the call graph
        # tools/lint_frames.py builds reaches the emitter from here.
        n = self._emit_arity[a]
        if n == 0:
            self._emit_fns[a]()
        elif n == 1:
            self._emit_fns[a](b)
        elif n == 2:
            self._emit_fns[a](b, c)
        else:
            self._emit_fns[a](b, c, d)

    def _s_load_id(self, a, b, c, d):
        if self.pass_ == E.MP_PASS_SCOPE:
            self.scope_cur.find_or_add_id(a, S.ID_INFO_KIND_GLOBAL_IMPLICIT)
        else:
            self._id_op(0, a)

    def _s_store_id(self, a, b, c, d):
        if self.pass_ == E.MP_PASS_SCOPE:
            self._get_id_for_modification(self.scope_cur, a)
        else:
            self._id_op(1, a)

    def _s_delete_id(self, a, b, c, d):
        if self.pass_ == E.MP_PASS_SCOPE:
            self._get_id_for_modification(self.scope_cur, a)
        else:
            self._id_op(2, a)

    def _s_load_const_obj(self, a, b, c, d):
        self.emit.load_const_obj(self.tree.const(a))

    def _s_error(self, a, b, c, d):
        self.syntax_error(a, _MESSAGES[b])

    def _s_setup_block(self, a, b, c, d):
        self.emit.setup_block(a, b)
        self.cur_except_level += 1
        if self.cur_except_level > self.scope_cur.exc_stack_size:
            self.scope_cur.exc_stack_size = self.cur_except_level

    def _s_end_block(self, a, b, c, d):
        self.cur_except_level -= 1
        self.emit.end_finally()

    def _s_loop_begin(self, a, b, c, d):
        self._loops.append(self.break_label)
        self._loops.append(self.continue_label)
        self._loops.append(self.break_continue_except_level)
        self.break_label = a | (E.MP_EMIT_BREAK_FROM_FOR if c else 0)
        self.continue_label = b
        self.break_continue_except_level = self.cur_except_level

    def _s_loop_end(self, a, b, c, d):
        self.break_continue_except_level = self._loops.pop()
        self.continue_label = self._loops.pop()
        self.break_label = self._loops.pop()

    def _s_break_cont(self, a, b, c, d):
        label = self.break_label if a else self.continue_label
        if label == INVALID_LABEL:
            self.syntax_error(b, "'break'/'continue' outside loop")
        self.emit.unwind_jump(
            label, self.cur_except_level - self.break_continue_except_level)

    def _s_set_generator(self, a, b, c, d):
        self.scopes[self.node_scope[a]].scope_flags |= \
            S.MP_SCOPE_FLAG_GENERATOR

    def _s_lambda_return(self, a, b, c, d):
        # A lambda that turned out to be a generator returns None, not the
        # value of its expression.  Whether it is one is only known once the
        # body has been walked, so this is a step rather than part of the plan.
        scope = self.scopes[a]
        if scope.scope_flags & S.MP_SCOPE_FLAG_GENERATOR:
            self.emit.pop_top()
            self.emit.load_const_tok(T.MP_TOKEN_KW_NONE)
        self.emit.return_value()

    def _s_class_return(self, a, b, c, d):
        # __class__ becomes a cell if the body closed over it, which is only
        # known after the body has been walked.
        scope = self.scopes[a]
        qst = self.pool.intern(b"__class__")
        i = scope.find(qst)
        if i < 0:
            raise AssertionError("class scope lost its __class__")
        if scope.id_kind(i) == S.ID_INFO_KIND_LOCAL:
            self.emit.load_const_tok(T.MP_TOKEN_KW_NONE)
        else:
            self.emit.load_local(qst, scope.id_local_num(i),
                                 E.MP_EMIT_IDOP_LOCAL_FAST)
        self.emit.return_value()

    # -- compile_node ----------------------------------------------------

    def _s_node(self, a, b, c, d):
        pn = a
        if PT.is_null(pn):
            return
        if PT.is_small_int(pn):
            self.emit.load_const_small_int(PT.small_int_value(pn))
            return
        if PT.is_leaf(pn):
            arg = PT.leaf_arg(pn)
            if PT.is_id(pn):
                self._s_load_id(arg, 0, 0, 0)
            elif PT.is_string(pn):
                self.emit.load_const_str(arg)
            elif arg != T.MP_TOKEN_NEWLINE:
                # file_input and single_input can let a NEWLINE through, for a
                # file that starts with one or a blank line at the REPL.
                self.emit.load_const_tok(arg)
            return
        kind = self.tree.struct_kind(pn)
        self.emit.set_source_line(self.tree.source_line(pn))
        plan = self._rules[kind](pn)
        if plan:
            self.plan(plan)

    # -- generic handlers ------------------------------------------------

    def h_generic_all_nodes(self, pn):
        tree = self.tree
        plan = []
        for i in range(tree.num_children(pn)):
            plan.append((W_NODE, tree.child(pn, i)))
        return plan

    def h_generic_tuple(self, pn):
        tree = self.tree
        n = tree.num_children(pn)
        plan = []
        for i in range(n):
            plan.append((W_NODE, tree.child(pn, i)))
        plan.append((W_EMIT, MI_BUILD, n, E.MP_EMIT_BUILD_TUPLE))
        return plan

    def h_const_object(self, pn):
        return [(W_LOAD_CONST_OBJ, self.tree.child(pn, 0))]

    # -- conditions ------------------------------------------------------

    def _s_if_cond(self, a, b, c, d):
        """c_if_cond: jump to *label* when *pn* is truthy iff *jump_if*."""
        pn, jump_if, label = a, b, c
        if self._is_const_false(pn):
            if not jump_if:
                self.emit.jump(label)
            return
        if self._is_const_true(pn):
            if jump_if:
                self.emit.jump(label)
            return
        tree = self.tree
        kind = self._kind(pn)
        if kind == G.RULE_not_test_2:
            self.plan([(W_IF_COND, tree.child(pn, 0), 0 if jump_if else 1,
                        label)])
            return
        if kind == G.RULE_or_test or kind == G.RULE_and_test:
            n = tree.num_children(pn)
            # "or" with jump_if false, and "and" with jump_if true, need a
            # second label to fall through to; the other way round each term
            # jumps straight to the caller's label.
            short_circuit = (kind == G.RULE_or_test) != bool(jump_if)
            plan = []
            if short_circuit:
                label2 = self.next_label_()
                inv = 0 if jump_if else 1
                for i in range(n - 1):
                    plan.append((W_IF_COND, tree.child(pn, i), inv, label2))
                plan.append((W_IF_COND, tree.child(pn, n - 1),
                             1 if jump_if else 0, label))
                plan.append((W_EMIT, MI_LABEL_ASSIGN, label2))
            else:
                for i in range(n):
                    plan.append((W_IF_COND, tree.child(pn, i),
                                 1 if jump_if else 0, label))
            self.plan(plan)
            return
        # Nothing special: compile it and test the result.
        self.plan([(W_NODE, pn),
                   (W_EMIT, MI_POP_JUMP_IF, 1 if jump_if else 0, label)])

    # -- assignment ------------------------------------------------------

    def _s_assign(self, a, b, c, d):
        """c_assign: store the top of the stack into *pn*."""
        pn, assign_kind = a, b
        if PT.is_null(pn):
            raise AssertionError("c_assign of a null node")
        if PT.is_leaf(pn):
            if not PT.is_id(pn):
                self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])
            arg = PT.leaf_arg(pn)
            if assign_kind == ASSIGN_AUG_LOAD:
                self._s_load_id(arg, 0, 0, 0)
            else:
                self._s_store_id(arg, 0, 0, 0)
            return

        tree = self.tree
        kind = tree.struct_kind(pn)
        if kind == G.RULE_atom_expr_normal:
            # The target is an index or an attribute.
            self.plan(self._assign_atom_expr(pn, assign_kind))
            return
        if kind == G.RULE_testlist_star_expr or kind == G.RULE_exprlist:
            if assign_kind != ASSIGN_STORE:
                self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])
            self._assign_tuple(pn, tree.num_children(pn))
            return
        if kind == G.RULE_atom_paren:
            inner = tree.child(pn, 0)
            if PT.is_null(inner) or assign_kind != ASSIGN_STORE:
                # "() = x" has nothing to assign to.
                self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])
            self._assign_testlist_comp(inner)
            return
        if kind == G.RULE_atom_bracket:
            if assign_kind != ASSIGN_STORE:
                self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])
            inner = tree.child(pn, 0)
            if PT.is_null(inner):
                # "[] = x" is allowed, and unpacks nothing.
                self._assign_tuple(pn, 0)
            elif self._is_kind(inner, G.RULE_testlist_comp):
                self._assign_testlist_comp(inner)
            else:
                # Brackets around one item.
                self._assign_tuple(pn, 1)
            return
        self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])

    def _assign_testlist_comp(self, pn):
        if self._testlist_comp_has_comp_for(pn):
            self.syntax_error(pn, _MESSAGES[MSG_CANNOT_ASSIGN])
        self._assign_tuple(pn, self.tree.num_children(pn))

    def _assign_tuple(self, container, n):
        """c_assign_tuple over the first *n* children of *container*."""
        tree = self.tree
        have_star = -1
        for i in range(n):
            if self._is_kind(tree.child(container, i), G.RULE_star_expr):
                if have_star == -1:
                    self.emit.unpack_ex(i, n - i - 1)
                    have_star = i
                else:
                    self.syntax_error(tree.child(container, i),
                                      "multiple *x in assignment")
        if have_star == -1:
            self.emit.unpack_sequence(n)
        plan = []
        for i in range(n):
            target = tree.child(container, i)
            if i == have_star:
                target = tree.child(target, 0)
            plan.append((W_ASSIGN, target, ASSIGN_STORE))
        self.plan(plan)

    def _assign_atom_expr(self, pn, assign_kind):
        """c_assign_atom_expr, as a plan."""
        tree = self.tree
        plan = []
        if assign_kind != ASSIGN_AUG_STORE:
            plan.append((W_NODE, tree.child(pn, 0)))
        trail = tree.child(pn, 1)
        if PT.is_struct(trail):
            if tree.struct_kind(trail) == G.RULE_atom_expr_trailers:
                n = tree.num_children(trail)
                if assign_kind != ASSIGN_AUG_STORE:
                    for i in range(n - 1):
                        plan.append((W_NODE, tree.child(trail, i)))
                trail = tree.child(trail, n - 1)
            kind = tree.struct_kind(trail)
            if kind == G.RULE_trailer_bracket:
                if assign_kind == ASSIGN_AUG_STORE:
                    plan.append((W_EMIT, MI_ROT_THREE))
                    plan.append((W_EMIT, MI_SUBSCR, E.MP_EMIT_SUBSCR_STORE))
                else:
                    plan.append((W_NODE, tree.child(trail, 0)))
                    if assign_kind == ASSIGN_AUG_LOAD:
                        plan.append((W_EMIT, MI_DUP_TOP_TWO))
                        plan.append((W_EMIT, MI_SUBSCR,
                                     E.MP_EMIT_SUBSCR_LOAD))
                    else:
                        plan.append((W_EMIT, MI_SUBSCR,
                                     E.MP_EMIT_SUBSCR_STORE))
                return plan
            if kind == G.RULE_trailer_period:
                qst = PT.leaf_arg(tree.child(trail, 0))
                if assign_kind == ASSIGN_AUG_LOAD:
                    plan.append((W_EMIT, MI_DUP_TOP))
                    plan.append((W_EMIT, MI_ATTR, qst, E.MP_EMIT_ATTR_LOAD))
                else:
                    if assign_kind == ASSIGN_AUG_STORE:
                        plan.append((W_EMIT, MI_ROT_TWO))
                    plan.append((W_EMIT, MI_ATTR, qst, E.MP_EMIT_ATTR_STORE))
                return plan
        # Not a subscript or attribute.  C has already emitted the code above
        # by the time it notices, so the error is queued behind it: an invalid
        # sub-expression must still be the error that gets reported.
        plan.append((W_ERROR, pn, MSG_CANNOT_ASSIGN))
        return plan

    # -- del -------------------------------------------------------------

    def _s_del(self, a, b, c, d):
        """c_del_stmt."""
        pn = a
        tree = self.tree
        if PT.is_id(pn):
            self._s_delete_id(PT.leaf_arg(pn), 0, 0, 0)
            return
        if self._is_kind(pn, G.RULE_atom_expr_normal):
            plan = [(W_NODE, tree.child(pn, 0))]
            trail = tree.child(pn, 1)
            if PT.is_struct(trail):
                if tree.struct_kind(trail) == G.RULE_atom_expr_trailers:
                    n = tree.num_children(trail)
                    for i in range(n - 1):
                        plan.append((W_NODE, tree.child(trail, i)))
                    trail = tree.child(trail, n - 1)
                kind = tree.struct_kind(trail)
                if kind == G.RULE_trailer_bracket:
                    plan.append((W_NODE, tree.child(trail, 0)))
                    plan.append((W_EMIT, MI_SUBSCR,
                                 E.MP_EMIT_SUBSCR_DELETE))
                    self.plan(plan)
                    return
                if kind == G.RULE_trailer_period:
                    plan.append((W_EMIT, MI_ATTR,
                                 PT.leaf_arg(tree.child(trail, 0)),
                                 E.MP_EMIT_ATTR_DELETE))
                    self.plan(plan)
                    return
            plan.append((W_ERROR, pn, MSG_CANNOT_DELETE))
            self.plan(plan)
            return
        if self._is_kind(pn, G.RULE_atom_paren):
            inner = tree.child(pn, 0)
            if PT.is_null(inner) or self._testlist_comp_has_comp_for(inner):
                self.syntax_error(pn, _MESSAGES[MSG_CANNOT_DELETE])
            plan = []
            for i in range(tree.num_children(inner)):
                plan.append((W_DEL, tree.child(inner, i)))
            self.plan(plan)
            return
        # Some statement that cannot be deleted, such as "del 1".
        self.syntax_error(pn, _MESSAGES[MSG_CANNOT_DELETE])

    def h_del_stmt(self, pn):
        plan = []
        for item in self._single_or_list(self.tree.child(pn, 0),
                                        G.RULE_exprlist):
            plan.append((W_DEL, item))
        return plan

    def _single_or_list(self, pn, list_kind):
        """apply_to_single_or_list's element list."""
        if self._is_kind(pn, list_kind):
            tree = self.tree
            out = []
            for i in range(tree.num_children(pn)):
                out.append(tree.child(pn, i))
            return out
        if PT.is_null(pn):
            return []
        return [pn]

    # -- scopes ----------------------------------------------------------

    def scope_new_and_link(self, kind, pn, emit_options, simple_name=None):
        """Create a scope and append it to the module's list.

        Returns its index: work items carry integers, and scopes are reached
        through this list.
        """
        if simple_name is None:
            scope = S.Scope(kind, pn, emit_options, pool=self.pool)
        else:
            scope = S.Scope(kind, pn, emit_options, simple_name=simple_name)
        scope.parent = self.scope_cur
        self.scopes.append(scope)
        return len(self.scopes) - 1

    def _s_close_over(self, a, b, c, d):
        """close_over_variables_etc: build the function or closure object."""
        this_scope = self.scopes[a]
        n_pos_defaults, n_kw_defaults = b, c
        if n_kw_defaults > 0:
            this_scope.scope_flags |= S.MP_SCOPE_FLAG_DEFKWARGS
        this_scope.num_def_pos_args = n_pos_defaults

        # Load the closed-over variables, in the order the *outer* scope
        # defines them (which is what CPython does).
        nfree = 0
        cur = self.scope_cur
        if cur.kind != S.SCOPE_MODULE:
            for i in range(cur.num_ids()):
                if cur.id_kind(i) in (S.ID_INFO_KIND_CELL,
                                      S.ID_INFO_KIND_FREE):
                    qst = cur.id_qst(i)
                    for j in range(this_scope.num_ids()):
                        if (this_scope.id_kind(j) == S.ID_INFO_KIND_FREE
                                and qst == this_scope.id_qst(j)):
                            # MicroPython loads closures with LOAD_FAST.
                            self.emit.load_local(qst, cur.id_local_num(i),
                                                 E.MP_EMIT_IDOP_LOCAL_FAST)
                            nfree += 1
        if nfree == 0:
            self.emit.make_function(this_scope, n_pos_defaults, n_kw_defaults)
        else:
            self.emit.make_closure(this_scope, nfree, n_pos_defaults,
                                   n_kw_defaults)

    # -- parameters and defaults -----------------------------------------

    def _s_params_begin(self, a, b, c, d):
        """compile_funcdef_lambdef: save the counters, then walk the params.

        A default value may itself be a lambda, whose own parameters would
        otherwise clobber these counters, so they are stacked.
        """
        scope_id, params_pn, list_kind = a, b, c
        st = self._param_state
        st.append(1 if self.have_star else 0)
        st.append(self.num_dict_params)
        st.append(self.num_default_params)
        self.have_star = False
        self.num_dict_params = 0
        self.num_default_params = 0
        plan = []
        for item in self._single_or_list(params_pn, list_kind):
            plan.append((W_PARAM, item, list_kind))
        plan.append((W_PARAMS_END, scope_id))
        self.plan(plan)

    def _s_param(self, a, b, c, d):
        """compile_funcdef_lambdef_param: emit one parameter's default."""
        pn = a
        tree = self.tree
        pn_kind = -1 if PT.is_id(pn) else tree.struct_kind(pn)

        if pn_kind in (G.RULE_typedargslist_star, G.RULE_varargslist_star):
            # Bare or named star; either way the rest are keyword-only.
            self.have_star = True
            return
        if pn_kind in (G.RULE_typedargslist_dbl_star,
                       G.RULE_varargslist_dbl_star):
            return

        if pn_kind == -1:
            pn_id = pn
            pn_equal = PT.NODE_NULL
        elif pn_kind == G.RULE_typedargslist_name:
            # A colon and/or equals specifier; nodes[1] is the annotation.
            pn_id = tree.child(pn, 0)
            pn_equal = tree.child(pn, 2)
        else:
            pn_id = tree.child(pn, 0)
            pn_equal = tree.child(pn, 1)

        if PT.is_null(pn_equal):
            # The parser allows a non-default parameter after a default one.
            if not self.have_star and self.num_default_params != 0:
                self.syntax_error(
                    pn, "non-default argument follows default argument")
            return

        if self.have_star:
            self.num_dict_params += 1
            if self.num_dict_params == 1:
                # The positional defaults go in a tuple, built here before
                # the map for the keyword defaults is started.
                if self.num_default_params > 0:
                    self.emit.build(self.num_default_params,
                                    E.MP_EMIT_BUILD_TUPLE)
                else:
                    self.emit.load_null()  # no default positional args
                self.emit.build(0, E.MP_EMIT_BUILD_MAP)
            # Value then key, then store into the map.
            self.plan([(W_NODE, pn_equal),
                       (W_EMIT, MI_LOAD_CONST_STR, PT.leaf_arg(pn_id)),
                       (W_EMIT, MI_STORE_MAP)])
        else:
            self.num_default_params += 1
            self.plan([(W_NODE, pn_equal)])

    def _s_params_end(self, a, b, c, d):
        if self.num_default_params > 0 and self.num_dict_params == 0:
            self.emit.build(self.num_default_params, E.MP_EMIT_BUILD_TUPLE)
            self.emit.load_null()  # no default keyword args
        n_pos = self.num_default_params
        n_kw = self.num_dict_params
        self.plan([(W_CLOSE_OVER, a, n_pos, n_kw)])
        st = self._param_state
        self.num_default_params = st.pop()
        self.num_dict_params = st.pop()
        self.have_star = bool(st.pop())

    # -- function, lambda and class definitions --------------------------

    def _def_name(self, pn):
        """The name a funcdef or classdef binds, which is also its scope's.

        Read straight from the parse node rather than from the scope, so that
        a decorated definition can plan the store before the scope exists.
        """
        return PT.leaf_arg(self.tree.child(pn, 0))

    def _s_funcdef(self, a, b, c, d):
        """compile_funcdef_helper: leave a function object on the stack.

        A step rather than part of the caller's plan, because the scope is
        created here and the creation order decides the order of the module's
        scopes -- which is the order their names enter the qstr table.  A
        decorator's own arguments may define scopes of their own (a lambda,
        say), and in C those come first.
        """
        pn, emit_options = a, b
        if self.pass_ == E.MP_PASS_SCOPE:
            self.node_scope[pn] = self.scope_new_and_link(
                S.SCOPE_FUNCTION, pn, emit_options, self._def_name(pn))
        sid = self.node_scope[pn]
        self.plan([(W_PARAMS_BEGIN, sid, self.tree.child(pn, 1),
                    G.RULE_typedargslist)])

    def _s_classdef(self, a, b, c, d):
        """compile_classdef_helper: leave a class object on the stack."""
        pn, emit_options = a, b
        tree = self.tree
        if self.pass_ == E.MP_PASS_SCOPE:
            self.node_scope[pn] = self.scope_new_and_link(
                S.SCOPE_CLASS, pn, emit_options, self._def_name(pn))
        sid = self.node_scope[pn]
        plan = [(W_EMIT, MI_LOAD_BUILD_CLASS),
                (W_CLOSE_OVER, sid, 0, 0),
                (W_EMIT, MI_LOAD_CONST_STR, self._def_name(pn))]
        # nodes[1] holds the parent classes.  "class C():" parses as an empty
        # classdef_2, which means no parents rather than one.
        parents = tree.child(pn, 1)
        if self._is_kind(parents, G.RULE_classdef_2):
            parents = PT.NODE_NULL
        plan.append((W_CALL_ARGS_BEGIN, parents, 0, 2))
        self.plan(plan)

    def h_funcdef(self, pn):
        return [(W_FUNCDEF, pn, self.scope_cur.emit_options),
                (W_STORE_ID, self._def_name(pn))]

    def h_classdef(self, pn):
        return [(W_CLASSDEF, pn, self.scope_cur.emit_options),
                (W_STORE_ID, self._def_name(pn))]

    def h_lambdef(self, pn):
        if self.pass_ == E.MP_PASS_SCOPE:
            self.node_scope[pn] = self.scope_new_and_link(
                S.SCOPE_LAMBDA, pn, self.scope_cur.emit_options)
        sid = self.node_scope[pn]
        return [(W_PARAMS_BEGIN, sid, self.tree.child(pn, 0),
                 G.RULE_varargslist)]

    def _built_in_decorator(self, name_nodes):
        """compile_built_in_decorator: ``(handled, emit_options)``.

        Only ``@micropython.bytecode`` is accepted: the others ask for native
        code or inline assembly, which this compiler does not emit.
        """
        if PT.leaf_arg(name_nodes[0]) != self.pool.intern(b"micropython"):
            return False, None
        if len(name_nodes) != 2:
            self.syntax_error(name_nodes[0], "invalid micropython decorator")
        attr = PT.leaf_arg(name_nodes[1])
        if attr == self.pool.intern(b"bytecode"):
            return True, E.MP_EMIT_OPT_BYTECODE
        for name in (b"native", b"viper", b"asm_thumb", b"asm_xtensa",
                     b"asm_rv32"):
            if attr == self.pool.intern(name):
                self.syntax_error(
                    name_nodes[1],
                    "this compiler emits only bytecode, not %s"
                    % name.decode("ascii"))
        self.syntax_error(name_nodes[1], "invalid micropython decorator")

    def h_decorated(self, pn):
        tree = self.tree
        decorators = self._extract_list(tree.child(pn, 0), G.RULE_decorators)
        # A function or class inherits the emit options of its definition
        # site, unless a built-in decorator changes them.
        emit_options = self.scope_cur.emit_options

        plan = []
        num_built_in = 0
        for dec in decorators:
            # nodes[0] is the decorator function, as a dotted name.
            name_nodes = self._extract_list(tree.child(dec, 0),
                                            G.RULE_dotted_name)
            handled, opt = self._built_in_decorator(name_nodes)
            if handled:
                emit_options = opt
                num_built_in += 1
                continue
            plan.append((W_NODE, name_nodes[0]))
            for j in range(1, len(name_nodes)):
                plan.append((W_EMIT, MI_ATTR, PT.leaf_arg(name_nodes[j]),
                             E.MP_EMIT_ATTR_LOAD))
            # nodes[1] holds the decorator's own arguments, if any.
            if not PT.is_null(tree.child(dec, 1)):
                plan.append((W_NODE, tree.child(dec, 1)))

        body = tree.child(pn, 1)
        body_kind = tree.struct_kind(body)
        if body_kind == G.RULE_funcdef:
            plan.append((W_FUNCDEF, body, emit_options))
            body_name = self._def_name(body)
        elif body_kind == G.RULE_async_funcdef:
            inner = tree.child(body, 0)
            plan.append((W_FUNCDEF, inner, emit_options))
            plan.append((W_SET_GENERATOR, inner))
            body_name = self._def_name(inner)
        else:
            plan.append((W_CLASSDEF, body, emit_options))
            body_name = self._def_name(body)

        for _ in range(len(decorators) - num_built_in):
            plan.append((W_EMIT, MI_CALL_FUNCTION, 1, 0, 0))
        plan.append((W_STORE_ID, body_name))
        return plan

    # -- declarations ----------------------------------------------------

    def _declare_global(self, pn, scope, i):
        if scope.id_kind(i) not in (S.ID_INFO_KIND_UNDECIDED,
                                    S.ID_INFO_KIND_GLOBAL_EXPLICIT):
            self.syntax_error(pn, "identifier redefined as global")
        scope.set_id_kind(i, S.ID_INFO_KIND_GLOBAL_EXPLICIT)
        # If the name exists in the outermost scope, make it explicit there
        # as well.
        outer, j = scope.find_global(scope.id_qst(i))
        if j >= 0:
            outer.set_id_kind(j, S.ID_INFO_KIND_GLOBAL_EXPLICIT)

    def _declare_nonlocal(self, pn, scope, i):
        if scope.id_kind(i) == S.ID_INFO_KIND_UNDECIDED:
            scope.set_id_kind(i, S.ID_INFO_KIND_GLOBAL_IMPLICIT)
            scope.check_to_close_over(i)
            if scope.id_kind(i) == S.ID_INFO_KIND_GLOBAL_IMPLICIT:
                self.syntax_error(pn, "no binding for nonlocal found")
        elif scope.id_kind(i) != S.ID_INFO_KIND_FREE:
            self.syntax_error(pn, "identifier redefined as nonlocal")

    def _declare_global_or_nonlocal(self, pn, scope, i, is_global):
        if is_global:
            self._declare_global(pn, scope, i)
        else:
            self._declare_nonlocal(pn, scope, i)

    def h_global_nonlocal_stmt(self, pn):
        if self.pass_ != E.MP_PASS_SCOPE:
            return None
        is_global = self.tree.struct_kind(pn) == G.RULE_global_stmt
        if not is_global and self.scope_cur.kind == S.SCOPE_MODULE:
            self.syntax_error(pn, "can't declare nonlocal in outer code")
        for node in self._extract_list(self.tree.child(pn, 0),
                                       G.RULE_name_list):
            qst = PT.leaf_arg(node)
            i = self.scope_cur.find_or_add_id(qst,
                                              S.ID_INFO_KIND_UNDECIDED)
            self._declare_global_or_nonlocal(pn, self.scope_cur, i, is_global)
        return None

    def _s_namedexpr(self, a, b, c, d):
        """The tail of compile_namedexpr_helper, after the value is on the
        stack: declare the target and store to it.

        The declaration must happen *after* the value expression has been
        walked, because identifiers are numbered in the order they are first
        mentioned and that numbering decides the local slots.
        """
        pn_name = a
        target = PT.leaf_arg(pn_name)
        cur = self.scope_cur
        # A name assigned by := inside a comprehension binds in the parent
        # scope, honouring any global/nonlocal declaration there (PEP 572).
        if self.pass_ == E.MP_PASS_SCOPE and S.scope_is_comp_like(cur.kind):
            j = self._get_id_for_modification(cur.parent, target)
            if cur.parent.id_kind(j) == S.ID_INFO_KIND_GLOBAL_EXPLICIT:
                cur.find_or_add_id(target, S.ID_INFO_KIND_GLOBAL_EXPLICIT)
            else:
                i = cur.find_or_add_id(target, S.ID_INFO_KIND_UNDECIDED)
                # The comprehension is defined in the outer scope.
                is_global = cur.parent.parent is None
                if (not is_global
                        and cur.id_kind(i)
                        == S.ID_INFO_KIND_GLOBAL_IMPLICIT):
                    # Already referenced, but now needs closing over, so
                    # reset the kind to make _declare_nonlocal do the work.
                    cur.set_id_kind(i, S.ID_INFO_KIND_UNDECIDED)
                self._declare_global_or_nonlocal(pn_name, cur, i, is_global)
        self._s_store_id(target, 0, 0, 0)

    def _namedexpr(self, plan, pn_name, pn_expr):
        if not PT.is_id(pn_name):
            self.syntax_error(pn_name, _MESSAGES[MSG_CANNOT_ASSIGN])
        plan.append((W_NODE, pn_expr))
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_NAMEDEXPR, pn_name))

    def h_namedexpr(self, pn):
        plan = []
        self._namedexpr(plan, self.tree.child(pn, 0), self.tree.child(pn, 1))
        return plan

    # -- simple statements -----------------------------------------------

    def h_break_cont_stmt(self, pn):
        is_break = self.tree.struct_kind(pn) == G.RULE_break_stmt
        return [(W_BREAK_CONT, 1 if is_break else 0, pn)]

    def h_return_stmt(self, pn):
        tree = self.tree
        if self.scope_cur.kind != S.SCOPE_FUNCTION:
            self.syntax_error(pn, "'return' outside function")
        arg = tree.child(pn, 0)
        if PT.is_null(arg):
            return [(W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE),
                    (W_EMIT, MI_RETURN_VALUE)]
        if self._is_kind(arg, G.RULE_test_if_expr):
            # "return a if c else b" returns from each branch, matching
            # CPython's optimisation.
            if_else = tree.child(arg, 1)
            l_fail = self.next_label_()
            return [(W_IF_COND, tree.child(if_else, 0), 0, l_fail),
                    (W_NODE, tree.child(arg, 0)),
                    (W_EMIT, MI_RETURN_VALUE),
                    (W_EMIT, MI_LABEL_ASSIGN, l_fail),
                    (W_NODE, tree.child(if_else, 1)),
                    (W_EMIT, MI_RETURN_VALUE)]
        return [(W_NODE, arg), (W_EMIT, MI_RETURN_VALUE)]

    def h_yield_stmt(self, pn):
        return [(W_NODE, self.tree.child(pn, 0)), (W_EMIT, MI_POP_TOP)]

    def h_raise_stmt(self, pn):
        tree = self.tree
        arg = tree.child(pn, 0)
        if PT.is_null(arg):
            return [(W_EMIT, MI_RAISE_VARARGS, 0)]
        if self._is_kind(arg, G.RULE_raise_stmt_arg):
            # raise x from y
            return [(W_NODE, tree.child(arg, 0)),
                    (W_NODE, tree.child(arg, 1)),
                    (W_EMIT, MI_RAISE_VARARGS, 2)]
        return [(W_NODE, arg), (W_EMIT, MI_RAISE_VARARGS, 1)]

    def _yield_from(self, plan):
        plan.append((W_EMIT, MI_GET_ITER, 0))
        plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
        plan.append((W_EMIT, MI_YIELD_, E.MP_EMIT_YIELD_FROM))

    def _await_object_method(self, plan, method):
        plan.append((W_EMIT, MI_LOAD_METHOD, method, 0))
        plan.append((W_EMIT, MI_CALL_METHOD, 0, 0, 0))
        self._yield_from(plan)

    def h_yield_expr(self, pn):
        if self.scope_cur.kind not in (S.SCOPE_FUNCTION, S.SCOPE_LAMBDA):
            self.syntax_error(pn, "'yield' outside function")
        tree = self.tree
        arg = tree.child(pn, 0)
        if PT.is_null(arg):
            return [(W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE),
                    (W_EMIT, MI_YIELD_, E.MP_EMIT_YIELD_VALUE)]
        if self._is_kind(arg, G.RULE_yield_arg_from):
            plan = [(W_NODE, tree.child(arg, 0))]
            self._yield_from(plan)
            return plan
        return [(W_NODE, arg), (W_EMIT, MI_YIELD_, E.MP_EMIT_YIELD_VALUE)]

    def h_star_expr(self, pn):
        self.syntax_error(pn, "*x must be assignment target")

    # -- imports ---------------------------------------------------------

    def _do_import_name(self, plan, pn):
        """do_import_name: import a name, returning the qstr it binds to."""
        tree = self.tree
        is_as = False
        q_base = 0
        if self._is_kind(pn, G.RULE_dotted_as_name):
            # "x as y": y is what gets bound.
            q_base = PT.leaf_arg(tree.child(pn, 1))
            pn = tree.child(pn, 0)
            is_as = True
        if PT.is_null(pn):
            # An empty name, as in "from . import x".
            q_empty = self.pool.intern(b"")
            plan.append((W_EMIT, MI_IMPORT_, q_empty, E.MP_EMIT_IMPORT_NAME))
            return q_empty
        if PT.is_id(pn):
            q_full = PT.leaf_arg(pn)
            if not is_as:
                q_base = q_full
            plan.append((W_EMIT, MI_IMPORT_, q_full, E.MP_EMIT_IMPORT_NAME))
            return q_base
        # A dotted name, a.b.c: the import takes the whole thing as one qstr.
        n = tree.num_children(pn)
        if not is_as:
            q_base = PT.leaf_arg(tree.child(pn, 0))
        parts = []
        for i in range(n):
            parts.append(self.pool.text(PT.leaf_arg(tree.child(pn, i))))
        q_full = self.pool.intern(b".".join(parts))
        plan.append((W_EMIT, MI_IMPORT_, q_full, E.MP_EMIT_IMPORT_NAME))
        if is_as:
            # "import a.b.c as d" binds the innermost module, so walk down.
            for i in range(1, n):
                plan.append((W_EMIT, MI_ATTR,
                             PT.leaf_arg(tree.child(pn, i)),
                             E.MP_EMIT_ATTR_LOAD))
        return q_base

    def h_import_name(self, pn):
        plan = []
        for item in self._single_or_list(self.tree.child(pn, 0),
                                        G.RULE_dotted_as_names):
            plan.append((W_EMIT, MI_LOAD_CONST_SMALL_INT, 0))  # level 0
            plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
            q_base = self._do_import_name(plan, item)
            plan.append((W_STORE_ID, q_base))
        return plan

    def h_import_from(self, pn):
        tree = self.tree
        source = tree.child(pn, 0)

        # Leading dots make the import relative; count them for the level.
        import_level = 0
        while True:
            if (PT.is_token(source)
                    or self._is_kind(source,
                                     G.RULE_one_or_more_period_or_ellipsis)):
                # "from .. import x": dots only, no module name.
                pn_rel = source
                source = PT.NODE_NULL
            elif self._is_kind(source, G.RULE_import_from_2b):
                # "from .foo import x": dots then a name.
                pn_rel = tree.child(source, 0)
                source = tree.child(source, 1)
            else:
                break
            for node in self._extract_list(
                    pn_rel, G.RULE_one_or_more_period_or_ellipsis):
                if PT.is_token_kind(node, T.MP_TOKEN_DEL_PERIOD):
                    import_level += 1
                else:
                    import_level += 3  # an ellipsis is three dots
            break

        plan = []
        if PT.is_token_kind(tree.child(pn, 1), T.MP_TOKEN_OP_STAR):
            if self.scope_cur.kind != S.SCOPE_MODULE:
                self.syntax_error(pn, "import * not at module level")
            plan.append((W_EMIT, MI_LOAD_CONST_SMALL_INT, import_level))
            # The "fromlist" is ("*",).
            plan.append((W_EMIT, MI_LOAD_CONST_STR, self.pool.intern(b"*")))
            plan.append((W_EMIT, MI_BUILD, 1, E.MP_EMIT_BUILD_TUPLE))
            self._do_import_name(plan, source)
            plan.append((W_EMIT, MI_IMPORT_, 0, E.MP_EMIT_IMPORT_STAR))
            return plan

        plan.append((W_EMIT, MI_LOAD_CONST_SMALL_INT, import_level))
        names = self._extract_list(tree.child(pn, 1), G.RULE_import_as_names)
        for item in names:
            plan.append((W_EMIT, MI_LOAD_CONST_STR,
                         PT.leaf_arg(tree.child(item, 0))))
        plan.append((W_EMIT, MI_BUILD, len(names), E.MP_EMIT_BUILD_TUPLE))
        self._do_import_name(plan, source)
        for item in names:
            qst = PT.leaf_arg(tree.child(item, 0))
            plan.append((W_EMIT, MI_IMPORT_, qst, E.MP_EMIT_IMPORT_FROM))
            as_name = tree.child(item, 1)
            if PT.is_null(as_name):
                plan.append((W_STORE_ID, qst))
            else:
                plan.append((W_STORE_ID, PT.leaf_arg(as_name)))
        plan.append((W_EMIT, MI_POP_TOP))
        return plan

    # -- assert, if, while, for ------------------------------------------

    def h_assert_stmt(self, pn):
        # Assertions are dropped entirely with optimisations on.
        if self.optimise_value != 0:
            return None
        tree = self.tree
        l_end = self.next_label_()
        plan = [(W_IF_COND, tree.child(pn, 0), 1, l_end)]
        # load_global rather than load_id, to agree with CPython.
        plan.append((W_EMIT, MI_LOAD_GLOBAL,
                     self.pool.intern(b"AssertionError"),
                     E.MP_EMIT_IDOP_GLOBAL_GLOBAL))
        msg = tree.child(pn, 1)
        if not PT.is_null(msg):
            plan.append((W_NODE, msg))
            plan.append((W_EMIT, MI_CALL_FUNCTION, 1, 0, 0))
        plan.append((W_EMIT, MI_RAISE_VARARGS, 1))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
        return plan

    def h_if_stmt(self, pn):
        tree = self.tree
        l_end = self.next_label_()
        plan = []
        cond = tree.child(pn, 0)
        done = False
        # "if False:" emits nothing at all.
        if not self._is_const_false(cond):
            l_fail = self.next_label_()
            plan.append((W_IF_COND, cond, 0, l_fail))
            plan.append((W_NODE, tree.child(pn, 1)))
            if self._is_const_true(cond):
                # "if True:" makes everything after it unreachable.
                done = True
            else:
                # No point jumping over elif/else blocks that are not there.
                if not (PT.is_null(tree.child(pn, 2))
                        and PT.is_null(tree.child(pn, 3))):
                    plan.append((W_EMIT, MI_JUMP, l_end))
                plan.append((W_EMIT, MI_LABEL_ASSIGN, l_fail))

        if not done:
            for elif_ in self._extract_list(tree.child(pn, 2),
                                            G.RULE_if_stmt_elif_list):
                cond = tree.child(elif_, 0)
                if self._is_const_false(cond):
                    continue
                l_fail = self.next_label_()
                plan.append((W_IF_COND, cond, 0, l_fail))
                plan.append((W_NODE, tree.child(elif_, 1)))
                if self._is_const_true(cond):
                    done = True
                    break
                plan.append((W_EMIT, MI_JUMP, l_end))
                plan.append((W_EMIT, MI_LABEL_ASSIGN, l_fail))

        if not done:
            plan.append((W_NODE, tree.child(pn, 3)))  # else, may be null
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
        return plan

    def h_while_stmt(self, pn):
        tree = self.tree
        break_label = self.next_label_()
        continue_label = self.next_label_()
        plan = [(W_LOOP_BEGIN, break_label, continue_label, 0)]
        cond = tree.child(pn, 0)
        if not self._is_const_false(cond):  # "while False:" emits nothing
            top_label = self.next_label_()
            if not self._is_const_true(cond):
                # "while True:" needs no initial jump to the condition.
                plan.append((W_EMIT, MI_JUMP, continue_label))
            plan.append((W_EMIT, MI_LABEL_ASSIGN, top_label))
            plan.append((W_NODE, tree.child(pn, 1)))  # body
            plan.append((W_EMIT, MI_LABEL_ASSIGN, continue_label))
            plan.append((W_IF_COND, cond, 1, top_label))
        # break/continue in the else block belong to any outer loop.
        plan.append((W_LOOP_END,))
        plan.append((W_NODE, tree.child(pn, 2)))  # else
        plan.append((W_EMIT, MI_LABEL_ASSIGN, break_label))
        return plan

    def _for_stmt_optimised_range(self, pn_var, pn_start, pn_end, pn_step,
                                  pn_body, pn_else):
        """compile_for_stmt_optimised_range.

        "for x in range(...)" with a constant step becomes an incremented
        variable, which needs no iterator object on the heap.  If <end> is a
        small-int constant the stack holds just the loop variable; otherwise
        <end> sits below it.
        """
        break_label = self.next_label_()
        continue_label = self.next_label_()
        top_label = self.next_label_()
        entry_label = self.next_label_()
        plan = [(W_LOOP_BEGIN, break_label, continue_label, 0)]

        end_on_stack = not PT.is_small_int(pn_end)
        if end_on_stack:
            plan.append((W_NODE, pn_end))
        plan.append((W_NODE, pn_start))
        plan.append((W_EMIT, MI_JUMP, entry_label))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, top_label))
        # Keep a copy of the next value and store it to the variable.
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_ASSIGN, pn_var, ASSIGN_STORE))
        plan.append((W_NODE, pn_body))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, continue_label))
        # var += step
        plan.append((W_NODE, pn_step))
        plan.append((W_EMIT, MI_BINARY_OP, R.MP_BINARY_OP_INPLACE_ADD))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, entry_label))
        # if var <cond> end: goto top
        if end_on_stack:
            plan.append((W_EMIT, MI_DUP_TOP_TWO))
            plan.append((W_EMIT, MI_ROT_TWO))
        else:
            plan.append((W_EMIT, MI_DUP_TOP))
            plan.append((W_NODE, pn_end))
        if PT.small_int_value(pn_step) >= 0:
            plan.append((W_EMIT, MI_BINARY_OP, R.MP_BINARY_OP_LESS))
        else:
            plan.append((W_EMIT, MI_BINARY_OP, R.MP_BINARY_OP_MORE))
        plan.append((W_EMIT, MI_POP_JUMP_IF, 1, top_label))
        plan.append((W_LOOP_END,))

        # The else block may contain break/continue, so the loop variables
        # have to come off the stack before it runs.
        end_label = 0
        if not PT.is_null(pn_else):
            plan.append((W_EMIT, MI_POP_TOP))
            if end_on_stack:
                plan.append((W_EMIT, MI_POP_TOP))
            plan.append((W_NODE, pn_else))
            end_label = self.next_label_()
            plan.append((W_EMIT, MI_JUMP, end_label))
            plan.append((W_EMIT, MI_ADJUST_STACK_SIZE,
                         1 + (1 if end_on_stack else 0)))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, break_label))
        # Drop the value that failed the loop condition, and <end> with it.
        plan.append((W_EMIT, MI_POP_TOP))
        if end_on_stack:
            plan.append((W_EMIT, MI_POP_TOP))
        if not PT.is_null(pn_else):
            plan.append((W_EMIT, MI_LABEL_ASSIGN, end_label))
        return plan

    def _try_optimise_range(self, pn):
        """The range arguments of an optimisable for-loop, or None."""
        tree = self.tree
        if not PT.is_id(tree.child(pn, 0)):
            return None
        it = tree.child(pn, 1)
        if not self._is_kind(it, G.RULE_atom_expr_normal):
            return None
        if not PT.is_id(tree.child(it, 0)):
            return None
        if PT.leaf_arg(tree.child(it, 0)) != self.pool.intern(b"range"):
            return None
        trailer = tree.child(it, 1)
        if self._kind(trailer) != G.RULE_trailer_paren:
            return None
        args = self._extract_list(tree.child(trailer, 0), G.RULE_arglist)
        if not 1 <= len(args) <= 3:
            return None
        if len(args) == 1:
            start = PT.new_small_int(0)
            end = args[0]
            step = PT.new_small_int(1)
        elif len(args) == 2:
            start, end, step = args[0], args[1], PT.new_small_int(1)
        else:
            start, end, step = args[0], args[1], args[2]
            # The step must be a non-zero constant for this to work.
            if (not PT.is_small_int(step)
                    or PT.small_int_value(step) == 0):
                return None
        # The arguments must be plain expressions, not *args, **kwargs or
        # keyword arguments.
        for node in (start, end):
            k = self._kind(node)
            if k in (G.RULE_arglist_star, G.RULE_arglist_dbl_star,
                     G.RULE_argument):
                return None
        return start, end, step

    def h_for_stmt(self, pn):
        tree = self.tree
        opt = self._try_optimise_range(pn)
        if opt is not None:
            return self._for_stmt_optimised_range(
                tree.child(pn, 0), opt[0], opt[1], opt[2],
                tree.child(pn, 2), tree.child(pn, 3))

        break_label = self.next_label_()
        continue_label = self.next_label_()
        pop_label = self.next_label_()
        plan = [(W_LOOP_BEGIN, break_label, continue_label, 1),
                (W_NODE, tree.child(pn, 1)),            # iterator
                (W_EMIT, MI_GET_ITER, 1),
                (W_EMIT, MI_LABEL_ASSIGN, continue_label),
                (W_EMIT, MI_FOR_ITER, pop_label),
                (W_ASSIGN, tree.child(pn, 0), ASSIGN_STORE),
                (W_NODE, tree.child(pn, 2)),            # body
                (W_EMIT, MI_JUMP, continue_label),
                (W_EMIT, MI_LABEL_ASSIGN, pop_label),
                (W_EMIT, MI_FOR_ITER_END),
                (W_LOOP_END,),
                (W_NODE, tree.child(pn, 3)),            # else
                (W_EMIT, MI_LABEL_ASSIGN, break_label)]
        return plan

    # -- try and with ----------------------------------------------------

    def _try_except(self, plan, pn_body, excepts, pn_else):
        """compile_try_except."""
        tree = self.tree
        l1 = self.next_label_()
        success_label = self.next_label_()
        plan.append((W_SETUP_BLOCK, l1, E.MP_EMIT_SETUP_BLOCK_EXCEPT))
        plan.append((W_NODE, pn_body))
        # Jump over the handler when the body completes.
        plan.append((W_EMIT, MI_POP_EXCEPT_JUMP, success_label, 0))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l1))
        plan.append((W_EMIT, MI_START_EXCEPT_HANDLER))
        # The raised exception instance is now on top of the stack.
        l2 = self.next_label_()

        n_except = len(excepts)
        for i in range(n_except):
            pns_except = excepts[i]
            qstr_exception_local = 0
            end_finally_label = self.next_label_()
            exc_expr = tree.child(pns_except, 0)
            if PT.is_null(exc_expr):
                # A catch-all handler, which must come last.
                if i + 1 != n_except:
                    self.syntax_error(pns_except,
                                      "default 'except' must be last")
            else:
                if PT.is_struct(exc_expr):
                    if tree.struct_kind(exc_expr) == G.RULE_try_stmt_as_name:
                        # The handler binds the exception to a name.
                        qstr_exception_local = PT.leaf_arg(
                            tree.child(exc_expr, 1))
                        exc_expr = tree.child(exc_expr, 0)
                plan.append((W_EMIT, MI_DUP_TOP))
                plan.append((W_NODE, exc_expr))
                plan.append((W_EMIT, MI_BINARY_OP,
                             R.MP_BINARY_OP_EXCEPTION_MATCH))
                plan.append((W_EMIT, MI_POP_JUMP_IF, 0, end_finally_label))

            # Either discard the exception instance or store it.
            if qstr_exception_local == 0:
                plan.append((W_EMIT, MI_POP_TOP))
            else:
                plan.append((W_STORE_ID, qstr_exception_local))

            # "except E as e" deletes e afterwards, per Python semantics, so
            # the handler body is wrapped in its own try-finally:
            #       try:
            #           <body>
            #       finally:
            #           e = None
            #           del e
            l3 = 0
            if qstr_exception_local != 0:
                l3 = self.next_label_()
                plan.append((W_SETUP_BLOCK, l3,
                             E.MP_EMIT_SETUP_BLOCK_FINALLY))
            plan.append((W_NODE, tree.child(pns_except, 1)))
            if qstr_exception_local != 0:
                plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
                plan.append((W_EMIT, MI_LABEL_ASSIGN, l3))
                # Room for a return value passing through the finally.
                plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 1))
                plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
                plan.append((W_STORE_ID, qstr_exception_local))
                plan.append((W_DELETE_ID, qstr_exception_local))
                plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, -1))
                plan.append((W_END_BLOCK,))

            plan.append((W_EMIT, MI_POP_EXCEPT_JUMP, l2, 1))
            plan.append((W_EMIT, MI_LABEL_ASSIGN, end_finally_label))
            # The next handler starts with the exception on the stack again.
            plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 1))

        plan.append((W_END_BLOCK,))
        plan.append((W_EMIT, MI_END_EXCEPT_HANDLER))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, success_label))
        plan.append((W_NODE, pn_else))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l2))

    def _try_finally(self, plan, pn_body, excepts, pn_else, pn_finally):
        """compile_try_finally."""
        l_finally_block = self.next_label_()
        plan.append((W_SETUP_BLOCK, l_finally_block,
                     E.MP_EMIT_SETUP_BLOCK_FINALLY))
        if not excepts:
            # Room for a possible UNWIND_JUMP state.
            plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 3))
            plan.append((W_NODE, pn_body))
            plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, -3))
        else:
            self._try_except(plan, pn_body, excepts, pn_else)
        # Reaching here means the try part finished normally, which the
        # runtime is told by a None sitting on the stack.
        plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
        # The finally block runs with the stack one deeper, in case it is
        # running as part of a return and the value is on top.
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_finally_block))
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 1))
        plan.append((W_NODE, pn_finally))
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, -1))
        plan.append((W_END_BLOCK,))

    def h_try_stmt(self, pn):
        tree = self.tree
        plan = []
        body = tree.child(pn, 0)
        rest = tree.child(pn, 1)
        kind = tree.struct_kind(rest)
        if kind == G.RULE_try_stmt_finally:
            self._try_finally(plan, body, [], PT.NODE_NULL,
                              tree.child(rest, 0))
        elif kind == G.RULE_try_stmt_except_and_more:
            excepts = self._extract_list(tree.child(rest, 0),
                                         G.RULE_try_stmt_except_list)
            finally_ = tree.child(rest, 2)
            if PT.is_null(finally_):
                self._try_except(plan, body, excepts, tree.child(rest, 1))
            else:
                self._try_finally(plan, body, excepts, tree.child(rest, 1),
                                  tree.child(finally_, 0))
        else:
            excepts = self._extract_list(rest, G.RULE_try_stmt_except_list)
            self._try_except(plan, body, excepts, PT.NODE_NULL)
        return plan

    def h_with_stmt(self, pn):
        """compile_with_stmt.

        C nests one call of compile_with_stmt_helper per item; the items are
        independent, so they flatten into a loop that opens each block in turn
        and closes them in reverse.
        """
        tree = self.tree
        items = self._extract_list(tree.child(pn, 0), G.RULE_with_stmt_list)
        plan = []
        labels = []
        for item in items:
            l_end = self.next_label_()
            labels.append(l_end)
            if self._is_kind(item, G.RULE_with_item):
                # "a as b"
                plan.append((W_NODE, tree.child(item, 0)))
                plan.append((W_SETUP_BLOCK, l_end,
                             E.MP_EMIT_SETUP_BLOCK_WITH))
                plan.append((W_ASSIGN, tree.child(item, 1), ASSIGN_STORE))
            else:
                plan.append((W_NODE, item))
                plan.append((W_SETUP_BLOCK, l_end,
                             E.MP_EMIT_SETUP_BLOCK_WITH))
                plan.append((W_EMIT, MI_POP_TOP))
        plan.append((W_NODE, tree.child(pn, 1)))  # body
        i = len(labels) - 1
        while i >= 0:
            plan.append((W_EMIT, MI_WITH_CLEANUP, labels[i]))
            plan.append((W_END_BLOCK,))
            i -= 1
        return plan

    # -- async ------------------------------------------------------------

    def _async_for_stmt(self, pn):
        tree = self.tree
        while_else_label = self.next_label_()
        try_exception_label = self.next_label_()
        try_else_label = self.next_label_()
        try_finally_label = self.next_label_()

        plan = []
        # Stack: (...) -> (..., iterable)
        plan.append((W_NODE, tree.child(pn, 1)))
        plan.append((W_EMIT, MI_LOAD_METHOD,
                     self.pool.intern(b"__aiter__"), 0))
        plan.append((W_EMIT, MI_CALL_METHOD, 0, 0, 0))

        break_label = self.next_label_()
        continue_label = self.next_label_()
        plan.append((W_LOOP_BEGIN, break_label, continue_label, 0))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, continue_label))
        plan.append((W_SETUP_BLOCK, try_exception_label,
                     E.MP_EMIT_SETUP_BLOCK_EXCEPT))
        plan.append((W_EMIT, MI_DUP_TOP))
        # yield from iterable.__anext__()
        self._await_object_method(plan, self.pool.intern(b"__anext__"))
        plan.append((W_ASSIGN, tree.child(pn, 0), ASSIGN_STORE))
        plan.append((W_EMIT, MI_POP_EXCEPT_JUMP, try_else_label, 0))

        plan.append((W_EMIT, MI_LABEL_ASSIGN, try_exception_label))
        plan.append((W_EMIT, MI_START_EXCEPT_HANDLER))
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_EMIT, MI_LOAD_GLOBAL,
                     self.pool.intern(b"StopAsyncIteration"),
                     E.MP_EMIT_IDOP_GLOBAL_GLOBAL))
        plan.append((W_EMIT, MI_BINARY_OP, R.MP_BINARY_OP_EXCEPTION_MATCH))
        plan.append((W_EMIT, MI_POP_JUMP_IF, 0, try_finally_label))
        plan.append((W_EMIT, MI_POP_TOP))  # the exception instance
        plan.append((W_EMIT, MI_POP_EXCEPT_JUMP, while_else_label, 1))

        plan.append((W_EMIT, MI_LABEL_ASSIGN, try_finally_label))
        # Arriving here, the exception is on the stack.
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 1))
        plan.append((W_END_BLOCK,))
        plan.append((W_EMIT, MI_END_EXCEPT_HANDLER))

        plan.append((W_EMIT, MI_LABEL_ASSIGN, try_else_label))
        plan.append((W_NODE, tree.child(pn, 2)))  # body
        plan.append((W_EMIT, MI_JUMP, continue_label))
        plan.append((W_LOOP_END,))

        plan.append((W_EMIT, MI_LABEL_ASSIGN, while_else_label))
        plan.append((W_NODE, tree.child(pn, 3)))  # else
        plan.append((W_EMIT, MI_LABEL_ASSIGN, break_label))
        plan.append((W_EMIT, MI_POP_TOP))  # the iterable
        return plan

    def _async_with_item(self, plan, item, l_labels):
        """One "async with" item: enter the context and open its finally."""
        tree = self.tree
        if self._is_kind(item, G.RULE_with_item):
            plan.append((W_NODE, tree.child(item, 0)))
            plan.append((W_EMIT, MI_DUP_TOP))
            self._await_object_method(plan, self.pool.intern(b"__aenter__"))
            plan.append((W_ASSIGN, tree.child(item, 1), ASSIGN_STORE))
        else:
            plan.append((W_NODE, item))
            plan.append((W_EMIT, MI_DUP_TOP))
            self._await_object_method(plan, self.pool.intern(b"__aenter__"))
            plan.append((W_EMIT, MI_POP_TOP))
        # __aexit__ is not preloaded as it is for a plain "with": the stack
        # cannot be reached more than three deep, so it is loaded below.
        plan.append((W_SETUP_BLOCK, l_labels[0],
                     E.MP_EMIT_SETUP_BLOCK_FINALLY))
        # Room for a possible UNWIND_JUMP state while the body runs.
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 3))

    def _async_with_tail(self, plan, l_labels):
        """The finally machinery that follows an "async with" body."""
        l_finally_block, l_aexit_no_exc, l_ret_unwind_jump, l_end = l_labels
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, -3))

        # Three ways out of the body:
        #  1. no exception, falling through here; stack: (..., ctx_mgr)
        #  2. an exception propagating out; stack: (..., ctx_mgr, exc)
        #  3. a return or unwind jump; stack: (..., ctx_mgr, X, INT)

        # Case 1: call __aexit__.
        plan.append((W_EMIT, MI_ASYNC_WITH_SETUP_FINALLY, l_aexit_no_exc,
                     l_finally_block, l_ret_unwind_jump))

        # Case 2: call __aexit__, then swallow or re-raise.
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_EMIT, MI_ROT_THREE))
        plan.append((W_EMIT, MI_ROT_TWO))
        plan.append((W_EMIT, MI_LOAD_METHOD,
                     self.pool.intern(b"__aexit__"), 0))
        plan.append((W_EMIT, MI_ROT_THREE))
        plan.append((W_EMIT, MI_ROT_THREE))
        plan.append((W_EMIT, MI_DUP_TOP))
        # MICROPY_CPYTHON_COMPAT: type(exc) comes from exc.__class__.
        plan.append((W_EMIT, MI_ATTR, self.pool.intern(b"__class__"),
                     E.MP_EMIT_ATTR_LOAD))
        plan.append((W_EMIT, MI_ROT_TWO))
        plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
        # Stack: (..., exc, __aexit__, ctx_mgr, type(exc), exc, None)
        plan.append((W_EMIT, MI_CALL_METHOD, 3, 0, 0))
        self._yield_from(plan)
        plan.append((W_EMIT, MI_POP_JUMP_IF, 0, l_end))
        plan.append((W_EMIT, MI_POP_TOP))  # the exception
        # Replace it with None, swallowing it.
        plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
        plan.append((W_EMIT, MI_JUMP, l_end))
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 2))

        # Case 3: call __aexit__.  Stack: (..., ctx_mgr, X, INT)
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_ret_unwind_jump))
        plan.append((W_EMIT, MI_ROT_THREE))
        plan.append((W_EMIT, MI_ROT_THREE))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_aexit_no_exc))
        # Reached from case 1 by a jump or from case 3 by falling through.
        plan.append((W_EMIT, MI_LOAD_METHOD,
                     self.pool.intern(b"__aexit__"), 0))
        plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_EMIT, MI_DUP_TOP))
        plan.append((W_EMIT, MI_CALL_METHOD, 3, 0, 0))
        self._yield_from(plan)
        plan.append((W_EMIT, MI_POP_TOP))
        plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, -1))

        # End of the finally block.  The stack holds one of:
        #  a. (..., None) - case 1, or case 2 with the exception swallowed
        #  b. (..., exc)  - case 2 with the exception re-raised
        #  c. (..., X, INT) - case 3
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
        plan.append((W_END_BLOCK,))

    def _async_with_stmt(self, pn):
        tree = self.tree
        items = self._extract_list(tree.child(pn, 0), G.RULE_with_stmt_list)
        plan = []
        all_labels = []
        for item in items:
            labels = (self.next_label_(), self.next_label_(),
                      self.next_label_(), self.next_label_())
            all_labels.append(labels)
            self._async_with_item(plan, item, labels)
        plan.append((W_NODE, tree.child(pn, 1)))  # body
        i = len(all_labels) - 1
        while i >= 0:
            self._async_with_tail(plan, all_labels[i])
            i -= 1
        return plan

    def h_async_stmt(self, pn):
        tree = self.tree
        inner = tree.child(pn, 0)
        kind = tree.struct_kind(inner)
        if kind == G.RULE_funcdef:
            return [(W_FUNCDEF, inner, self.scope_cur.emit_options),
                    (W_STORE_ID, self._def_name(inner)),
                    (W_SET_GENERATOR, inner)]
        # async for/with is only legal inside an async function.
        if not (self.scope_cur.scope_flags & S.MP_SCOPE_FLAG_GENERATOR):
            self.syntax_error(inner, "async for/with outside async function")
        if kind == G.RULE_for_stmt:
            return self._async_for_stmt(inner)
        return self._async_with_stmt(inner)

    def h_atom_expr_await(self, pn):
        if self.scope_cur.kind not in (S.SCOPE_FUNCTION, S.SCOPE_LAMBDA):
            self.syntax_error(pn, "'await' outside function")
        plan = self.h_atom_expr_normal(pn)
        self._yield_from(plan)
        return plan

    # -- expression statements -------------------------------------------

    def h_expr_stmt(self, pn):
        tree = self.tree
        lhs = tree.child(pn, 0)
        rhs = tree.child(pn, 1)

        if PT.is_null(rhs):
            if self.is_repl and self.scope_cur.kind == S.SCOPE_MODULE:
                # At the REPL an expression statement is printed.
                return [(W_LOAD_ID, self.pool.intern(b"__repl_print__")),
                        (W_NODE, lhs),
                        (W_EMIT, MI_CALL_FUNCTION, 1, 0, 0),
                        (W_EMIT, MI_POP_TOP)]
            if ((PT.is_leaf(lhs) and not PT.is_id(lhs))
                    or self._is_kind(lhs, G.RULE_const_object)):
                # A lonely constant does nothing at all.
                return None
            return [(W_NODE, lhs), (W_EMIT, MI_POP_TOP)]

        if PT.is_struct(rhs):
            kind = tree.struct_kind(rhs)
            if kind == G.RULE_annassign:
                # nodes[0] is the annotation, which is ignored.
                if PT.is_null(tree.child(rhs, 1)):
                    # A bare "x: y".  Inside a function that declares x local.
                    if (self.scope_cur.kind == S.SCOPE_FUNCTION
                            and PT.is_id(lhs)):
                        self.scope_cur.find_or_add_id(PT.leaf_arg(lhs),
                                                      S.ID_INFO_KIND_LOCAL)
                    return None
                # "x: y = z" assigns as usual.
                rhs = tree.child(rhs, 1)
            elif kind == G.RULE_expr_stmt_augassign:
                tok = PT.leaf_arg(tree.child(rhs, 0))
                op = (R.MP_BINARY_OP_INPLACE_OR
                      + (tok - T.MP_TOKEN_DEL_PIPE_EQUAL))
                return [(W_ASSIGN, lhs, ASSIGN_AUG_LOAD),
                        (W_NODE, tree.child(rhs, 1)),
                        (W_EMIT, MI_BINARY_OP, op),
                        (W_ASSIGN, lhs, ASSIGN_AUG_STORE)]
            elif kind == G.RULE_expr_stmt_assign_list:
                # "a = b = c": the value is the last node, and following
                # CPython the left-most target is stored first.
                last = tree.num_children(rhs) - 1
                plan = [(W_NODE, tree.child(rhs, last))]
                if last > 0:
                    plan.append((W_EMIT, MI_DUP_TOP))
                plan.append((W_ASSIGN, lhs, ASSIGN_STORE))
                for i in range(last):
                    if i + 1 < last:
                        plan.append((W_EMIT, MI_DUP_TOP))
                    plan.append((W_ASSIGN, tree.child(rhs, i), ASSIGN_STORE))
                return plan

        # A plain assignment.  "a, b = c, d" and "a, b, c = d, e, f" avoid
        # building and unpacking a tuple, when the two sides have the same
        # length and the left has no star expression.
        if (self._is_kind(rhs, G.RULE_testlist_star_expr)
                and self._is_kind(lhs, G.RULE_testlist_star_expr)):
            n = tree.num_children(lhs)
            if n == tree.num_children(rhs) and n in (2, 3):
                starred = False
                for i in range(n):
                    if self._is_kind(tree.child(lhs, i), G.RULE_star_expr):
                        starred = True
                if not starred:
                    plan = [(W_NODE, tree.child(rhs, 0)),
                            (W_NODE, tree.child(rhs, 1))]
                    if n == 3:
                        plan.append((W_NODE, tree.child(rhs, 2)))
                        plan.append((W_EMIT, MI_ROT_THREE))
                    plan.append((W_EMIT, MI_ROT_TWO))
                    for i in range(n):
                        plan.append((W_ASSIGN, tree.child(lhs, i),
                                     ASSIGN_STORE))
                    return plan

        return [(W_NODE, rhs), (W_ASSIGN, lhs, ASSIGN_STORE)]

    # -- expressions -----------------------------------------------------

    def h_test_if_expr(self, pn):
        tree = self.tree
        if_else = tree.child(pn, 1)
        l_fail = self.next_label_()
        l_end = self.next_label_()
        return [(W_IF_COND, tree.child(if_else, 0), 0, l_fail),
                (W_NODE, tree.child(pn, 0)),          # success value
                (W_EMIT, MI_JUMP, l_end),
                (W_EMIT, MI_LABEL_ASSIGN, l_fail),
                (W_EMIT, MI_ADJUST_STACK_SIZE, -1),
                (W_NODE, tree.child(if_else, 1)),     # failure value
                (W_EMIT, MI_LABEL_ASSIGN, l_end)]

    def h_or_and_test(self, pn):
        tree = self.tree
        cond = 1 if tree.struct_kind(pn) == G.RULE_or_test else 0
        l_end = self.next_label_()
        n = tree.num_children(pn)
        plan = []
        for i in range(n):
            plan.append((W_NODE, tree.child(pn, i)))
            if i + 1 < n:
                plan.append((W_EMIT, MI_JUMP_IF_OR_POP, cond, l_end))
        plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
        return plan

    def h_not_test_2(self, pn):
        return [(W_NODE, self.tree.child(pn, 0)),
                (W_EMIT, MI_UNARY_OP, R.MP_UNARY_OP_NOT)]

    def h_comparison(self, pn):
        tree = self.tree
        num = tree.num_children(pn)
        plan = [(W_NODE, tree.child(pn, 0))]
        multi = num > 3
        l_fail = 0
        if multi:
            l_fail = self.next_label_()
        i = 1
        while i + 1 < num:
            plan.append((W_NODE, tree.child(pn, i + 1)))
            if i + 2 < num:
                # A chained comparison keeps the middle operand.
                plan.append((W_EMIT, MI_DUP_TOP))
                plan.append((W_EMIT, MI_ROT_THREE))
            op_node = tree.child(pn, i)
            if PT.is_token(op_node):
                tok = PT.leaf_arg(op_node)
                if tok == T.MP_TOKEN_KW_IN:
                    op = R.MP_BINARY_OP_IN
                else:
                    op = R.MP_BINARY_OP_LESS + (tok - T.MP_TOKEN_OP_LESS)
            else:
                kind = tree.struct_kind(op_node)
                if kind == G.RULE_comp_op_not_in:
                    op = R.MP_BINARY_OP_NOT_IN
                elif PT.is_null(tree.child(op_node, 0)):
                    op = R.MP_BINARY_OP_IS
                else:
                    op = R.MP_BINARY_OP_IS_NOT
            plan.append((W_EMIT, MI_BINARY_OP, op))
            if i + 2 < num:
                plan.append((W_EMIT, MI_JUMP_IF_OR_POP, 0, l_fail))
            i += 2
        if multi:
            l_end = self.next_label_()
            plan.append((W_EMIT, MI_JUMP, l_end))
            plan.append((W_EMIT, MI_LABEL_ASSIGN, l_fail))
            plan.append((W_EMIT, MI_ADJUST_STACK_SIZE, 1))
            plan.append((W_EMIT, MI_ROT_TWO))
            plan.append((W_EMIT, MI_POP_TOP))
            plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
        return plan

    def h_binary_op(self, pn):
        # expr, xor_expr and and_expr sit in the same order as the matching
        # mp_binary_op_t values, which is what makes the arithmetic valid.
        tree = self.tree
        op = R.MP_BINARY_OP_OR + tree.struct_kind(pn) - G.RULE_expr
        num = tree.num_children(pn)
        plan = [(W_NODE, tree.child(pn, 0))]
        for i in range(1, num):
            plan.append((W_NODE, tree.child(pn, i)))
            plan.append((W_EMIT, MI_BINARY_OP, op))
        return plan

    def h_term(self, pn):
        tree = self.tree
        num = tree.num_children(pn)
        plan = [(W_NODE, tree.child(pn, 0))]
        i = 1
        while i + 1 < num:
            plan.append((W_NODE, tree.child(pn, i + 1)))
            tok = PT.leaf_arg(tree.child(pn, i))
            op = R.MP_BINARY_OP_LSHIFT + (tok - T.MP_TOKEN_OP_DBL_LESS)
            plan.append((W_EMIT, MI_BINARY_OP, op))
            i += 2
        return plan

    def h_factor_2(self, pn):
        tree = self.tree
        tok = PT.leaf_arg(tree.child(pn, 0))
        if tok == T.MP_TOKEN_OP_TILDE:
            op = R.MP_UNARY_OP_INVERT
        else:
            op = R.MP_UNARY_OP_POSITIVE + (tok - T.MP_TOKEN_OP_PLUS)
        return [(W_NODE, tree.child(pn, 1)), (W_EMIT, MI_UNARY_OP, op)]

    def h_power(self, pn):
        plan = self.h_generic_all_nodes(pn)
        plan.append((W_EMIT, MI_BINARY_OP, R.MP_BINARY_OP_POWER))
        return plan

    # -- trailers and calls ----------------------------------------------

    def h_atom_expr_normal(self, pn):
        tree = self.tree
        plan = [(W_NODE, tree.child(pn, 0))]

        # compile_atom_expr_await reaches here with a null trailer.
        trail = tree.child(pn, 1)
        if PT.is_null(trail):
            return plan

        # The trailers, as a list of struct nodes.
        if tree.struct_kind(trail) == G.RULE_atom_expr_trailers:
            trailers = []
            for i in range(tree.num_children(trail)):
                trailers.append(tree.child(trail, i))
        else:
            trailers = [trail]
        num_trail = len(trailers)

        i = 0
        base = tree.child(pn, 0)
        if (self.scope_cur.kind == S.SCOPE_FUNCTION
                and PT.is_id(base)
                and PT.leaf_arg(base) == self.pool.intern(b"super")
                and tree.struct_kind(trailers[0]) == G.RULE_trailer_paren
                and PT.is_null(tree.child(trailers[0], 0))):
            # A bare "super()" inside a function.  Load the class it should
            # search upwards from, then the function's first argument, which
            # is assumed to be self.
            plan.append((W_LOAD_ID, self.pool.intern(b"__class__")))
            found = False
            for i in range(self.scope_cur.num_ids()):
                if self.scope_cur.id_flags(i) & S.ID_FLAG_IS_PARAM:
                    plan.append((W_LOAD_ID, self.scope_cur.id_qst(i)))
                    found = True
                    break
            if not found:
                # Really a TypeError, but reported here.
                self.syntax_error(trailers[0], "super() can't find self")
            if (num_trail >= 3
                    and tree.struct_kind(trailers[1]) == G.RULE_trailer_period
                    and tree.struct_kind(trailers[2]) == G.RULE_trailer_paren):
                # "super().f(...)" as a method call, which allocates nothing.
                plan.append((W_EMIT, MI_LOAD_METHOD,
                             PT.leaf_arg(tree.child(trailers[1], 0)), 1))
                plan.append((W_CALL_ARGS_BEGIN,
                             tree.child(trailers[2], 0), 1, 0))
                i = 3
            else:
                plan.append((W_EMIT, MI_CALL_FUNCTION, 2, 0, 0))
                i = 1
        elif (COMP_ORDEREDDICT_LITERAL
                and PT.is_id(base)
                and PT.leaf_arg(base) == self.pool.intern(b"OrderedDict")
                and tree.struct_kind(trailers[0]) == G.RULE_trailer_paren
                and self._is_kind(tree.child(trailers[0], 0),
                                  G.RULE_atom_brace)):
            # "OrderedDict({...})" fills the new mapping directly.
            plan.append((W_EMIT, MI_CALL_FUNCTION, 0, 0, 0))
            self._atom_brace_helper(plan, tree.child(trailers[0], 0), False)
            i = 1

        while i < num_trail:
            if (i + 1 < num_trail
                    and tree.struct_kind(trailers[i]) == G.RULE_trailer_period
                    and tree.struct_kind(trailers[i + 1])
                    == G.RULE_trailer_paren):
                # "a.f(...)" as a method call, following PyPy.
                plan.append((W_EMIT, MI_LOAD_METHOD,
                             PT.leaf_arg(tree.child(trailers[i], 0)), 0))
                plan.append((W_CALL_ARGS_BEGIN,
                             tree.child(trailers[i + 1], 0), 1, 0))
                i += 2
            else:
                plan.append((W_NODE, trailers[i]))
                i += 1
        return plan

    def h_trailer_paren(self, pn):
        return [(W_CALL_ARGS_BEGIN, self.tree.child(pn, 0), 0, 0)]

    def h_trailer_bracket(self, pn):
        # The object being indexed is on top of the stack.
        return [(W_NODE, self.tree.child(pn, 0)),
                (W_EMIT, MI_SUBSCR, E.MP_EMIT_SUBSCR_LOAD)]

    def h_trailer_period(self, pn):
        return [(W_EMIT, MI_ATTR, PT.leaf_arg(self.tree.child(pn, 0)),
                 E.MP_EMIT_ATTR_LOAD)]

    def _s_call_args_begin(self, a, b, c, d):
        """compile_trailer_paren_helper: start counting a call's arguments.

        The counts have to survive the walk of every argument expression, so
        they live on the state stack rather than in a C local.
        """
        args = self._extract_list(a, G.RULE_arglist)
        st = self._state
        st.append(c)   # n_positional, seeded with the extra ones
        st.append(0)   # n_keyword
        st.append(0)   # star_flags
        st.append(0)   # star_args bitmap
        plan = []
        for i in range(len(args)):
            plan.append((W_CALL_ARG, args[i], i))
        plan.append((W_CALL_ARGS_END, b))
        self.plan(plan)

    def _s_call_arg(self, a, b, c, d):
        """One argument of a call in progress."""
        pn, index = a, b
        tree = self.tree
        st = self._state
        base = len(st) - 4   # n_positional, n_keyword, star_flags, star_args

        kind = self._kind(pn)
        if kind == G.RULE_arglist_star:
            if st[base + 2] & E.MP_EMIT_STAR_FLAG_DOUBLE:
                self.syntax_error(pn, "* arg after **")
            if st[base + 1]:
                # *arg after a keyword argument is a CPython feature left out
                # of MicroPython to save code size.
                self.syntax_error(pn, "* arg after kwarg")
            if index >= MP_SMALL_INT_BITS - 1:
                # The star-args bitmap is a small int, so this many arguments
                # cannot be represented.
                self.syntax_error(pn, "too many args")
            st[base + 2] |= E.MP_EMIT_STAR_FLAG_SINGLE
            st[base + 3] |= 1 << index
            st[base] += 1
            self.plan([(W_NODE, tree.child(pn, 0))])
            return
        if kind == G.RULE_arglist_dbl_star:
            st[base + 2] |= E.MP_EMIT_STAR_FLAG_DOUBLE
            # A double-star argument is stored as a keyword with a null key.
            st[base + 1] += 1
            self.plan([(W_EMIT, MI_LOAD_NULL),
                       (W_NODE, tree.child(pn, 0))])
            return
        if kind == G.RULE_argument:
            second = tree.child(pn, 1)
            if self._is_kind(second, G.RULE_argument_3):
                # "f(x := 1)"
                st[base] += 1
                plan = []
                self._namedexpr(plan, tree.child(pn, 0),
                                tree.child(second, 0))
                self.plan(plan)
                return
            if not self._is_kind(second, G.RULE_comp_for):
                if not PT.is_id(tree.child(pn, 0)):
                    self.syntax_error(pn, "LHS of keyword arg must be an id")
                st[base + 1] += 1
                self.plan([(W_EMIT, MI_LOAD_CONST_STR,
                            PT.leaf_arg(tree.child(pn, 0))),
                           (W_NODE, second)])
                return
            # A bare generator expression, as in "sum(x for x in y)".
            st[base] += 1
            self.plan([(W_COMPREHENSION, pn, S.SCOPE_GEN_EXPR)])
            return

        # An ordinary positional argument.
        if st[base + 2] & E.MP_EMIT_STAR_FLAG_DOUBLE:
            self.syntax_error(pn, "positional arg after **")
        if st[base + 1] > 0:
            self.syntax_error(pn, "positional arg after keyword arg")
        st[base] += 1
        self.plan([(W_NODE, pn)])

    def _s_call_args_end(self, a, b, c, d):
        st = self._state
        star_args = st.pop()
        star_flags = st.pop()
        n_keyword = st.pop()
        n_positional = st.pop()
        if star_flags != 0:
            # One more object, holding the map of which args were starred.
            self.emit.load_const_small_int(star_args)
        if a:
            self.emit.call_method(n_positional, n_keyword, star_flags)
        else:
            self.emit.call_function(n_positional, n_keyword, star_flags)

    # -- comprehensions --------------------------------------------------

    def _s_comprehension(self, a, b, c, d):
        """compile_comprehension: call a hidden function with the iterator.

        A comprehension is its own scope, so the code here just builds the
        function object and calls it; the loop itself is compiled as that
        scope's body, by ``_comp_iter``.
        """
        pn, kind = a, b
        tree = self.tree
        pns_comp_for = tree.child(pn, 1)
        if self.pass_ == E.MP_PASS_SCOPE:
            self.node_scope[pns_comp_for] = self.scope_new_and_link(
                kind, pn, self.scope_cur.emit_options)
        sid = self.node_scope[pns_comp_for]
        plan = [(W_CLOSE_OVER, sid, 0, 0),
                (W_NODE, tree.child(pns_comp_for, 1))]  # the iterator source
        if kind == S.SCOPE_GEN_EXPR:
            plan.append((W_EMIT, MI_GET_ITER, 0))
        plan.append((W_EMIT, MI_CALL_FUNCTION, 1, 0, 0))
        self.plan(plan)

    def _comp_iter(self, plan, pns_comp_for, pn_inner_expr):
        """compile_scope_comp_iter: the body of a comprehension's scope.

        C recurses once per nested ``for``; the nesting is a chain, so it
        flattens into a loop that opens each level and then closes them in
        reverse.
        """
        tree = self.tree
        tails = []
        cur = pns_comp_for
        depth = 0
        while True:
            l_top = self.next_label_()
            l_end = self.next_label_()
            plan.append((W_EMIT, MI_LABEL_ASSIGN, l_top))
            plan.append((W_EMIT, MI_FOR_ITER, l_end))
            plan.append((W_ASSIGN, tree.child(cur, 0), ASSIGN_STORE))
            tails.append((l_top, l_end))
            pn_iter = tree.child(cur, 2)
            # Any number of "if" clauses, each jumping back to the top.
            while (not PT.is_null(pn_iter)
                   and tree.struct_kind(pn_iter) == G.RULE_comp_if):
                plan.append((W_IF_COND, tree.child(pn_iter, 0), 0, l_top))
                pn_iter = tree.child(pn_iter, 1)
            if PT.is_null(pn_iter):
                # Innermost level: produce one element.
                plan.append((W_NODE, pn_inner_expr))
                if self.scope_cur.kind == S.SCOPE_GEN_EXPR:
                    plan.append((W_EMIT, MI_YIELD_, E.MP_EMIT_YIELD_VALUE))
                    plan.append((W_EMIT, MI_POP_TOP))
                else:
                    plan.append((W_EMIT, MI_STORE_COMP,
                                 self.scope_cur.kind, 4 * depth + 5))
                break
            # Another "for": get its iterator and go a level deeper.
            plan.append((W_NODE, tree.child(pn_iter, 1)))
            plan.append((W_EMIT, MI_GET_ITER, 1))
            cur = pn_iter
            depth += 1
        i = len(tails) - 1
        while i >= 0:
            l_top, l_end = tails[i]
            plan.append((W_EMIT, MI_JUMP, l_top))
            plan.append((W_EMIT, MI_LABEL_ASSIGN, l_end))
            plan.append((W_EMIT, MI_FOR_ITER_END))
            i -= 1

    # -- atoms -----------------------------------------------------------

    def h_atom_paren(self, pn):
        tree = self.tree
        inner = tree.child(pn, 0)
        if PT.is_null(inner):
            return [(W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_TUPLE)]
        if self._testlist_comp_has_comp_for(inner):
            return [(W_COMPREHENSION, inner, S.SCOPE_GEN_EXPR)]
        return self.h_generic_tuple(inner)

    def h_atom_bracket(self, pn):
        tree = self.tree
        inner = tree.child(pn, 0)
        if PT.is_null(inner):
            return [(W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_LIST)]
        if self._is_kind(inner, G.RULE_testlist_comp):
            if self._testlist_comp_has_comp_for(inner):
                return [(W_COMPREHENSION, inner, S.SCOPE_LIST_COMP)]
            plan = self.h_generic_all_nodes(inner)
            plan.append((W_EMIT, MI_BUILD, tree.num_children(inner),
                         E.MP_EMIT_BUILD_LIST))
            return plan
        return [(W_NODE, inner), (W_EMIT, MI_BUILD, 1,
                                  E.MP_EMIT_BUILD_LIST)]

    def _atom_brace_helper(self, plan, pn, create_map):
        """compile_atom_brace_helper: a dict, a set, or a comprehension."""
        tree = self.tree
        first = tree.child(pn, 0)
        if PT.is_null(first):
            # {} is an empty dict.
            if create_map:
                plan.append((W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_MAP))
            return
        if not PT.is_struct(first):
            # A set with one element.
            plan.append((W_NODE, first))
            plan.append((W_EMIT, MI_BUILD, 1, E.MP_EMIT_BUILD_SET))
            return
        kind = tree.struct_kind(first)
        if kind == G.RULE_dictorsetmaker_item:
            # A dict with one element.
            if create_map:
                plan.append((W_EMIT, MI_BUILD, 1, E.MP_EMIT_BUILD_MAP))
            plan.append((W_NODE, first))
            plan.append((W_EMIT, MI_STORE_MAP))
            return
        if kind != G.RULE_dictorsetmaker:
            # A set with one element.
            plan.append((W_NODE, first))
            plan.append((W_EMIT, MI_BUILD, 1, E.MP_EMIT_BUILD_SET))
            return

        head = tree.child(first, 0)
        rest = tree.child(first, 1)
        if tree.struct_kind(rest) == G.RULE_comp_for:
            # A dict or set comprehension; the first element says which.
            if self._is_kind(head, G.RULE_dictorsetmaker_item):
                plan.append((W_COMPREHENSION, first, S.SCOPE_DICT_COMP))
            else:
                plan.append((W_COMPREHENSION, first, S.SCOPE_SET_COMP))
            return

        # A dict or set with several elements; again the first one decides.
        items = self._extract_list(tree.child(rest, 0),
                                   G.RULE_dictorsetmaker_list2)
        n = len(items)
        is_dict = self._is_kind(head, G.RULE_dictorsetmaker_item)
        if is_dict:
            if create_map:
                plan.append((W_EMIT, MI_BUILD, 1 + n, E.MP_EMIT_BUILD_MAP))
            plan.append((W_NODE, head))
            plan.append((W_EMIT, MI_STORE_MAP))
        else:
            plan.append((W_NODE, head))

        for item in items:
            is_key_value = self._is_kind(item, G.RULE_dictorsetmaker_item)
            if is_dict and not is_key_value:
                self.syntax_error(first, "expecting key:value for dict")
            if not is_dict and is_key_value:
                self.syntax_error(first, "expecting just a value for set")
            plan.append((W_NODE, item))
            if is_dict:
                plan.append((W_EMIT, MI_STORE_MAP))
        if not is_dict:
            plan.append((W_EMIT, MI_BUILD, 1 + n, E.MP_EMIT_BUILD_SET))

    def h_atom_brace(self, pn):
        plan = []
        self._atom_brace_helper(plan, pn, True)
        return plan

    def h_dictorsetmaker_item(self, pn):
        # A dict key:value pair: the value is pushed first.
        return [(W_NODE, self.tree.child(pn, 1)),
                (W_NODE, self.tree.child(pn, 0))]

    def h_subscript(self, pn):
        """compile_subscript: the slice forms inside [].

        The parse shapes are subscript_2 ("x:..."), subscript_3 (":..."),
        subscript_3c ("::...") and subscript_3d (":x:...").
        """
        tree = self.tree
        plan = []
        if tree.struct_kind(pn) == G.RULE_subscript_2:
            plan.append((W_NODE, tree.child(pn, 0)))  # start of the slice
            pn = tree.child(pn, 1)
        else:
            # subscript_3: the start of the slice is None.
            plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))

        inner = tree.child(pn, 0)
        if PT.is_null(inner):
            # [?:]
            plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
            plan.append((W_EMIT, MI_BUILD, 2, E.MP_EMIT_BUILD_SLICE))
            return plan
        if PT.is_struct(inner):
            kind = tree.struct_kind(inner)
            if kind == G.RULE_subscript_3c:
                plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
                step = tree.child(inner, 0)
                if PT.is_null(step):
                    # [?::]
                    plan.append((W_EMIT, MI_BUILD, 2,
                                 E.MP_EMIT_BUILD_SLICE))
                else:
                    # [?::x]
                    plan.append((W_NODE, step))
                    plan.append((W_EMIT, MI_BUILD, 3,
                                 E.MP_EMIT_BUILD_SLICE))
                return plan
            if kind == G.RULE_subscript_3d:
                plan.append((W_NODE, tree.child(inner, 0)))
                sliceop = tree.child(inner, 1)
                step = tree.child(sliceop, 0)
                if PT.is_null(step):
                    # [?:x:]
                    plan.append((W_EMIT, MI_BUILD, 2,
                                 E.MP_EMIT_BUILD_SLICE))
                else:
                    # [?:x:x]
                    plan.append((W_NODE, step))
                    plan.append((W_EMIT, MI_BUILD, 3,
                                 E.MP_EMIT_BUILD_SLICE))
                return plan
        # [?:x]
        plan.append((W_NODE, inner))
        plan.append((W_EMIT, MI_BUILD, 2, E.MP_EMIT_BUILD_SLICE))
        return plan

    # -- parameter declarations (the scope pass) -------------------------

    def _declare_param(self, pn, pn_name, pn_star, pn_dbl_star):
        """compile_scope_func_lambda_param: add one parameter to the scope.

        Runs only in the scope pass, and before the body, so that arguments
        get the lowest local numbers.
        """
        tree = self.tree
        scope = self.scope_cur
        # **kw has to be the last parameter.
        if scope.scope_flags & S.MP_SCOPE_FLAG_VARKEYWORDS:
            self.syntax_error(pn, "invalid syntax")

        param_name = 0   # MP_QSTRnull: no name to declare
        param_flag = S.ID_FLAG_IS_PARAM
        if PT.is_id(pn):
            param_name = PT.leaf_arg(pn)
            if self.have_star:
                scope.num_kwonly_args += 1
            else:
                scope.num_pos_args += 1
        else:
            kind = tree.struct_kind(pn)
            if kind == pn_name:
                # A named parameter with a possible annotation.
                param_name = PT.leaf_arg(tree.child(pn, 0))
                if self.have_star:
                    scope.num_kwonly_args += 1
                else:
                    scope.num_pos_args += 1
            elif kind == pn_star:
                if self.have_star:
                    self.syntax_error(pn, "invalid syntax")  # two stars
                self.have_star = True
                param_flag = S.ID_FLAG_IS_PARAM | S.ID_FLAG_IS_STAR_PARAM
                inner = tree.child(pn, 0)
                if PT.is_null(inner):
                    pass  # a bare star declares nothing
                elif PT.is_id(inner):
                    scope.scope_flags |= S.MP_SCOPE_FLAG_VARARGS
                    param_name = PT.leaf_arg(inner)
                else:
                    # A named star with an annotation (a tfpdef).
                    scope.scope_flags |= S.MP_SCOPE_FLAG_VARARGS
                    param_name = PT.leaf_arg(tree.child(inner, 0))
            else:
                # A double star, with a possible annotation.
                param_name = PT.leaf_arg(tree.child(pn, 0))
                param_flag = (S.ID_FLAG_IS_PARAM
                              | S.ID_FLAG_IS_DBL_STAR_PARAM)
                scope.scope_flags |= S.MP_SCOPE_FLAG_VARKEYWORDS

        if param_name != 0:
            i = scope.find_or_add_id(param_name, S.ID_INFO_KIND_UNDECIDED)
            if scope.id_kind(i) != S.ID_INFO_KIND_UNDECIDED:
                self.syntax_error(pn, "argument name reused")
            scope.set_id_kind(i, S.ID_INFO_KIND_LOCAL)
            scope.set_id_flags(i, param_flag)

    def _declare_params(self, params_pn, list_kind):
        self.have_star = False
        if list_kind == G.RULE_typedargslist:
            names = (G.RULE_typedargslist_name, G.RULE_typedargslist_star,
                     G.RULE_typedargslist_dbl_star)
        else:
            names = (G.RULE_varargslist_name, G.RULE_varargslist_star,
                     G.RULE_varargslist_dbl_star)
        for item in self._single_or_list(params_pn, list_kind):
            self._declare_param(item, names[0], names[1], names[2])

    # -- one pass over one scope -----------------------------------------

    def compile_scope(self, scope, pass_):
        """Walk *scope* once.  False means the emitter wants another pass."""
        self.pass_ = pass_
        self.scope_cur = scope
        self.next_label = 0
        self.cur_except_level = 0
        self.break_label = INVALID_LABEL
        self.continue_label = INVALID_LABEL
        self.break_continue_except_level = 0
        self.have_star = False
        self.num_dict_params = 0
        self.num_default_params = 0
        self.emit_common.start_pass(pass_)
        self.emit.start_pass(pass_, scope)
        if pass_ == E.MP_PASS_SCOPE:
            # The maxima are computed by the passes that follow.
            scope.stack_size = 0
            scope.exc_stack_size = 0

        tree = self.tree
        plan = []
        if self._is_kind(scope.pn, G.RULE_eval_input):
            plan.append((W_NODE, tree.child(scope.pn, 0)))
            plan.append((W_EMIT, MI_RETURN_VALUE))
        elif scope.kind == S.SCOPE_MODULE:
            # MICROPY_ENABLE_DOC_STRING is 0, so there is no __doc__ to store
            # and the parser has already turned a leading string statement
            # into a pass.
            plan.append((W_NODE, scope.pn))
            plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
            plan.append((W_EMIT, MI_RETURN_VALUE))
        elif scope.kind == S.SCOPE_FUNCTION:
            if pass_ == E.MP_PASS_SCOPE:
                self._declare_params(tree.child(scope.pn, 1),
                                     G.RULE_typedargslist)
            plan.append((W_NODE, tree.child(scope.pn, 3)))  # body
            plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
            plan.append((W_EMIT, MI_RETURN_VALUE))
        elif scope.kind == S.SCOPE_LAMBDA:
            plan.append((W_EMIT, MI_SET_SOURCE_LINE,
                         tree.source_line(scope.pn)))
            if pass_ == E.MP_PASS_SCOPE:
                self._declare_params(tree.child(scope.pn, 0),
                                     G.RULE_varargslist)
            plan.append((W_NODE, tree.child(scope.pn, 1)))  # body
            # A generator lambda returns None rather than its expression,
            # which is only known once the body has been walked.
            plan.append((W_LAMBDA_RETURN, self.scopes.index(scope)))
        elif S.scope_is_comp_like(scope.kind):
            pns_comp_for = tree.child(scope.pn, 1)
            # The iterator is passed in as the one argument.  CPython names it
            # ".0"; any name a user cannot write will do, and the blank qstr
            # already exists.
            qstr_arg = self.pool.intern(b"")
            if pass_ == E.MP_PASS_SCOPE:
                scope.find_or_add_id(qstr_arg, S.ID_INFO_KIND_LOCAL)
                scope.num_pos_args = 1
            plan.append((W_EMIT, MI_SET_SOURCE_LINE,
                         tree.source_line(scope.pn)))
            if scope.kind == S.SCOPE_LIST_COMP:
                plan.append((W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_LIST))
            elif scope.kind == S.SCOPE_DICT_COMP:
                plan.append((W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_MAP))
            elif scope.kind == S.SCOPE_SET_COMP:
                plan.append((W_EMIT, MI_BUILD, 0, E.MP_EMIT_BUILD_SET))
            if scope.kind == S.SCOPE_GEN_EXPR:
                # Four stack slots for the iterator, the first NULL to say
                # the second points at the iterator object itself.
                plan.append((W_EMIT, MI_LOAD_NULL))
                plan.append((W_LOAD_ID, qstr_arg))
                plan.append((W_EMIT, MI_LOAD_NULL))
                plan.append((W_EMIT, MI_LOAD_NULL))
            else:
                plan.append((W_LOAD_ID, qstr_arg))
                plan.append((W_EMIT, MI_GET_ITER, 1))
            self._comp_iter(plan, pns_comp_for, tree.child(scope.pn, 0))
            if scope.kind == S.SCOPE_GEN_EXPR:
                plan.append((W_EMIT, MI_LOAD_CONST_TOK, T.MP_TOKEN_KW_NONE))
            plan.append((W_EMIT, MI_RETURN_VALUE))
        else:
            qst_class = self.pool.intern(b"__class__")
            if pass_ == E.MP_PASS_SCOPE:
                scope.find_or_add_id(qst_class, S.ID_INFO_KIND_LOCAL)
            plan.append((W_LOAD_ID, self.pool.intern(b"__name__")))
            plan.append((W_STORE_ID, self.pool.intern(b"__module__")))
            plan.append((W_EMIT, MI_LOAD_CONST_STR,
                         PT.leaf_arg(tree.child(scope.pn, 0))))
            plan.append((W_STORE_ID, self.pool.intern(b"__qualname__")))
            plan.append((W_NODE, tree.child(scope.pn, 2)))  # body
            plan.append((W_CLASS_RETURN, self.scopes.index(scope)))

        self.plan(plan)
        self.run()
        complete = self.emit.end_pass()
        if self.cur_except_level != 0:
            raise AssertionError("except level ends at %d"
                                 % self.cur_except_level)
        return complete


def scope_compute_things(scope, qst_class):
    """scope_compute_things: assign every identifier its slot number.

    *qst_class* is the qstr number of ``__class__``, which a class scope
    treats specially; it comes from the pool, which this function does not
    otherwise need.
    """
    # MicroPython puts the *x parameter after the other parameters, except
    # **y, so swap it into place.
    if scope.scope_flags & S.MP_SCOPE_FLAG_VARARGS:
        id_param = -1
        for i in range(scope.num_ids() - 1, -1, -1):
            flags = scope.id_flags(i)
            if flags & S.ID_FLAG_IS_STAR_PARAM:
                if id_param >= 0:
                    scope.swap_ids(id_param, i)
                break
            elif id_param < 0 and flags == S.ID_FLAG_IS_PARAM:
                id_param = i

    scope.num_locals = 0
    # In a function an implicit global becomes an explicit one, and
    # every local gets its number.
    for i in range(scope.num_ids()):
        if scope.kind == S.SCOPE_CLASS and scope.id_qst(i) == qst_class:
            # __class__ is not a local; if it is used it becomes a cell.
            continue
        if (S.scope_is_func_like(scope.kind)
                and scope.id_kind(i) == S.ID_INFO_KIND_GLOBAL_IMPLICIT):
            scope.set_id_kind(i, S.ID_INFO_KIND_GLOBAL_EXPLICIT)
        # A parameter always counts as one local, even when it is a cell.
        if (scope.id_kind(i) == S.ID_INFO_KIND_LOCAL
                or scope.id_is_param(i)):
            scope.set_id_local_num(i, scope.num_locals)
            scope.num_locals += 1

    # Cells come right after the fast locals.  Parameters are not counted
    # again here: they stay at the start of the locals even when they are
    # cells.
    for i in range(scope.num_ids()):
        if (scope.id_kind(i) == S.ID_INFO_KIND_CELL
                and not scope.id_is_param(i)):
            scope.set_id_local_num(i, scope.num_locals)
            scope.num_locals += 1

    # Free variables are numbered in the order the parent scope defines
    # them, and come before everything else.
    if scope.parent is not None:
        num_free = 0
        parent = scope.parent
        for i in range(parent.num_ids()):
            if parent.id_kind(i) in (S.ID_INFO_KIND_CELL,
                                     S.ID_INFO_KIND_FREE):
                qst = parent.id_qst(i)
                for j in range(scope.num_ids()):
                    if (scope.id_kind(j) == S.ID_INFO_KIND_FREE
                            and qst == scope.id_qst(j)):
                        scope.set_id_local_num(j, num_free)
                        num_free += 1
        if num_free > 0:
            for i in range(scope.num_ids()):
                if (scope.id_kind(i) != S.ID_INFO_KIND_FREE
                        or scope.id_is_param(i)):
                    scope.set_id_local_num(
                        i, scope.id_local_num(i) + num_free)
            # Free variables are passed in like parameters.
            scope.num_pos_args += num_free
            scope.num_locals += num_free


# -- the module driver ----------------------------------------------------

def compile_to_scopes(tree, source_file, is_repl=False, optimise_value=0,
                     on_pass=None):
    """mp_compile_to_raw_code: run every pass over every scope.

    Returns ``(compiler, module_scope)``.  Each scope ends up with its
    ``raw_code`` bytes and the child scopes its code refers to.

    *on_pass*, if given, is called after each pass over each scope.  The peak
    heap is reached in the middle of the run, with the parse tree, the scopes
    and the emitted code all live at once, so it cannot be seen from outside;
    ``tests/compile_heap.py`` samples it through this.
    """
    comp = Compiler(tree, source_file, is_repl, optimise_value)
    # The module's own scope.  Its emit options are the VM default, which for
    # a bytecode-only compiler means nothing in particular.
    module_sid = comp.scope_new_and_link(S.SCOPE_MODULE, tree.root,
                                         E.MP_EMIT_OPT_NONE)

    # The scope pass, which discovers nested scopes as it goes; the list
    # grows underneath this loop, exactly as C's linked list does.
    max_num_labels = 0
    i = 0
    while i < len(comp.scopes):
        scope = comp.scopes[i]
        comp.compile_scope(scope, E.MP_PASS_SCOPE)
        # A name that was only ever read may still belong to an enclosing
        # function, which is only now knowable.
        for j in range(scope.num_ids()):
            if scope.id_kind(j) == S.ID_INFO_KIND_GLOBAL_IMPLICIT:
                scope.check_to_close_over(j)
        if comp.next_label > max_num_labels:
            max_num_labels = comp.next_label
        if on_pass is not None:
            on_pass(comp, scope, E.MP_PASS_SCOPE)
        i += 1

    qst_class = comp.pool.intern(b"__class__")
    for scope in comp.scopes:
        scope_compute_things(scope, qst_class)

    comp.emit.set_max_num_labels(max_num_labels)

    for scope in comp.scopes:
        comp.compile_scope(scope, E.MP_PASS_STACK_SIZE)
        comp.compile_scope(scope, E.MP_PASS_CODE_SIZE)
        # The emit pass may report that the code shrank, and has to be redone
        # until the size settles.
        while not comp.compile_scope(scope, E.MP_PASS_EMIT):
            pass
        if on_pass is not None:
            on_pass(comp, scope, E.MP_PASS_EMIT)

    return comp, comp.scopes[module_sid]


def build_raw_code(module_scope):
    """The raw-code tree for an ``.mpy``, built bottom-up without recursion."""
    order = []
    stack = [module_scope]
    while stack:
        scope = stack.pop()
        order.append(scope)
        for child in scope.raw_code_children:
            stack.append(child)
    built = {}
    i = len(order) - 1
    while i >= 0:
        scope = order[i]
        children = []
        for child in scope.raw_code_children:
            children.append(built[id(child)])
        built[id(scope)] = MPY.RawCode(0, scope.raw_code, children)
        i -= 1
    return built[id(module_scope)]


def compile_to_mpy(tree, source_file, is_repl=False, optimise_value=0):
    """Compile a parse tree into an ``MpyFile``."""
    comp, module_scope = compile_to_scopes(tree, source_file, is_repl,
                                           optimise_value)
    objs = []
    for value in comp.emit_common.const_objs:
        objs.append(MPY.obj_from_value(value))
    return MPY.MpyFile(SMALL_INT_BITS,
                       comp.emit_common.qstr_table.entries(),
                       objs, build_raw_code(module_scope))


def compile_source(source, filename="<stdin>", mode="exec",
                   optimise_value=0):
    """Source text to ``.mpy`` bytes.

    *mode* is "exec", "eval" or "single", as ``compile()`` uses them.
    """
    from . import parser
    if mode == "eval":
        input_kind = parser.PARSE_EVAL_INPUT
        is_repl = False
    elif mode == "single":
        input_kind = parser.PARSE_SINGLE_INPUT
        is_repl = True
    elif mode == "exec":
        input_kind = parser.PARSE_FILE_INPUT
        is_repl = False
    else:
        raise ValueError("mode must be 'exec', 'eval' or 'single'")
    tree = parser.parse(source, filename, input_kind, optimise_value)
    name = filename if type(filename) is bytes else filename.encode("utf-8")
    mpy = compile_to_mpy(tree, name, is_repl, optimise_value)
    return MPY.encode(mpy)
