"""Scopes, parameters, definitions and declarations.

``def``, ``lambda`` and ``class``: making a scope, declaring its parameters,
evaluating its defaults and decorators, and closing over it.  Also the
``global`` and ``nonlocal`` statements.  Part of the chain of partial classes
described in ``mpyc/compile.py``.
"""

from . import emitbc as E
from . import grammar as G
from . import parsetree as PT
from . import scope as S
from .compile_defs import (
    MI_DUP_TOP, MI_STORE_MAP, MI_LOAD_BUILD_CLASS, MI_LOAD_CONST_STR, MI_ATTR,
    MI_CALL_FUNCTION, W_EMIT, W_NODE, W_STORE_ID, W_CLOSE_OVER,
    W_PARAMS_BEGIN, W_PARAM, W_PARAMS_END, W_CALL_ARGS_BEGIN, W_NAMEDEXPR,
    W_SET_GENERATOR, W_FUNCDEF, W_CLASSDEF, MSG_CANNOT_ASSIGN, _MESSAGES
)
from .compile_assign import AssignWalker


class DefnWalker(AssignWalker):
    """Scopes, parameters, definitions and declarations."""

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
