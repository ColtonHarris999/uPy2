"""Simple statements, imports, and the loops that are not blocks.

``pass`` through ``return``, the two ``import`` forms, and ``assert``, ``if``,
``while`` and ``for``.  Part of the chain of partial classes described in
``mpyc/compile.py``.
"""

from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from . import runtime0 as R
from . import scope as S
from . import tokens as T
from .compile_defs import (
    ASSIGN_STORE, MI_POP_TOP, MI_DUP_TOP, MI_DUP_TOP_TWO, MI_ROT_TWO,
    MI_RETURN_VALUE, MI_FOR_ITER_END, MI_LOAD_CONST_TOK,
    MI_LOAD_CONST_SMALL_INT, MI_LOAD_CONST_STR, MI_BINARY_OP, MI_LABEL_ASSIGN,
    MI_JUMP, MI_FOR_ITER, MI_ADJUST_STACK_SIZE, MI_RAISE_VARARGS, MI_YIELD_,
    MI_GET_ITER, MI_ATTR, MI_BUILD, MI_POP_JUMP_IF, MI_LOAD_METHOD,
    MI_IMPORT_, MI_CALL_FUNCTION, MI_CALL_METHOD, MI_LOAD_GLOBAL, W_EMIT,
    W_NODE, W_IF_COND, W_ASSIGN, W_STORE_ID, W_LOOP_BEGIN, W_LOOP_END,
    W_BREAK_CONT
)
from .compile_defn import DefnWalker


class StmtWalker(DefnWalker):
    """Simple statements, imports, assert, if, while and for."""

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
