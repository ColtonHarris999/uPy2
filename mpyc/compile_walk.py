"""The trampoline: the work stack, the dispatch and the steps.

This is the base of the chain of partial classes that makes up the walker;
``mpyc/compile.py`` has the whole picture and the rest of the chain.  What is
here is the machinery every slice above uses: the explicit work stack, the
step and rule dispatch tables, the parse-node and identifier helpers, and the
handlers small enough to have no section of their own.
"""

from array import array

from . import constnode as CN
from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from . import scope as S
from . import tokens as T
from .emitbc import Emit, EmitCommon
from .compile_defs import (
    INVALID_LABEL, CompileError, _EMIT_METHODS, MI_BUILD, W_EMIT, W_NODE,
    W_LOAD_CONST_OBJ, _WORK_STRIDE, _MESSAGES
)


class Walker:
    """The work stack, the dispatch tables and the steps."""

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
