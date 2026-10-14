"""Conditions, assignment and ``del``.

``c_if_cond``, ``c_assign`` and ``c_del_stmt``: the three walks that take a
node apart rather than evaluating it.  Part of the chain of partial classes
described in ``mpyc/compile.py``.
"""

from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from .compile_defs import (
    ASSIGN_STORE, ASSIGN_AUG_LOAD, ASSIGN_AUG_STORE, MI_DUP_TOP,
    MI_DUP_TOP_TWO, MI_ROT_TWO, MI_ROT_THREE, MI_LABEL_ASSIGN, MI_SUBSCR,
    MI_ATTR, MI_POP_JUMP_IF, W_EMIT, W_NODE, W_IF_COND, W_ASSIGN, W_DEL,
    W_ERROR, MSG_CANNOT_ASSIGN, MSG_CANNOT_DELETE, _MESSAGES
)
from .compile_walk import Walker


class AssignWalker(Walker):
    """c_if_cond, c_assign and c_del_stmt."""

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
