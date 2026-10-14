"""``try``, ``with``, ``async`` and expression statements.

The statements that push an exception-handling block, their ``async``
rewrites, and the statement that is just an expression.  Part of the chain of
partial classes described in ``mpyc/compile.py``.
"""

from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from . import runtime0 as R
from . import scope as S
from . import tokens as T
from .compile_defs import (
    ASSIGN_STORE, ASSIGN_AUG_LOAD, ASSIGN_AUG_STORE, MI_POP_TOP, MI_DUP_TOP,
    MI_ROT_TWO, MI_ROT_THREE, MI_START_EXCEPT_HANDLER, MI_END_EXCEPT_HANDLER,
    MI_LOAD_CONST_TOK, MI_BINARY_OP, MI_LABEL_ASSIGN, MI_JUMP,
    MI_ADJUST_STACK_SIZE, MI_WITH_CLEANUP, MI_ATTR, MI_POP_JUMP_IF,
    MI_POP_EXCEPT_JUMP, MI_LOAD_METHOD, MI_CALL_FUNCTION, MI_CALL_METHOD,
    MI_ASYNC_WITH_SETUP_FINALLY, MI_LOAD_GLOBAL, W_EMIT, W_NODE, W_ASSIGN,
    W_LOAD_ID, W_STORE_ID, W_DELETE_ID, W_SETUP_BLOCK, W_END_BLOCK,
    W_LOOP_BEGIN, W_LOOP_END, W_SET_GENERATOR, W_FUNCDEF
)
from .compile_stmt import StmtWalker


class BlockWalker(StmtWalker):
    """try, with, async, and expression statements."""

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
