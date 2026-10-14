"""Expressions: operators, trailers, calls, comprehensions and atoms.

Everything that leaves a value on the stack, from ``or`` down to a literal.
Part of the chain of partial classes described in ``mpyc/compile.py``.
"""

from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from . import runtime0 as R
from . import scope as S
from . import tokens as T
from .compile_defs import (
    MP_SMALL_INT_BITS, COMP_ORDEREDDICT_LITERAL, ASSIGN_STORE, MI_POP_TOP,
    MI_DUP_TOP, MI_ROT_TWO, MI_ROT_THREE, MI_LOAD_NULL, MI_FOR_ITER_END,
    MI_STORE_MAP, MI_LOAD_CONST_TOK, MI_LOAD_CONST_STR, MI_UNARY_OP,
    MI_BINARY_OP, MI_LABEL_ASSIGN, MI_JUMP, MI_FOR_ITER, MI_ADJUST_STACK_SIZE,
    MI_YIELD_, MI_GET_ITER, MI_SUBSCR, MI_ATTR, MI_BUILD, MI_JUMP_IF_OR_POP,
    MI_STORE_COMP, MI_LOAD_METHOD, MI_CALL_FUNCTION, W_EMIT, W_NODE,
    W_IF_COND, W_ASSIGN, W_LOAD_ID, W_CLOSE_OVER, W_CALL_ARGS_BEGIN,
    W_CALL_ARG, W_CALL_ARGS_END, W_COMPREHENSION
)
from .compile_block import BlockWalker


class ExprWalker(BlockWalker):
    """Operators, trailers, calls, comprehensions and atoms."""

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
