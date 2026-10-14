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
The walker is split across modules, as a chain of partial classes, because
the compiler has to be able to compile itself.  The heap a compile needs is
roughly linear in the module's line count, and the walker in one piece was
2886 lines -- fourteen times the 200-line module the budget is written for,
and it peaked at 451 KB where the budget is 64 KB.
Each module holds one slice of ``Compiler`` and inherits the one before it:

* ``compile_defs``   -- the constants, the exception, the work-item kinds
* ``compile_walk``   -- ``Walker``: the work stack, the dispatch, the steps
* ``compile_assign`` -- ``AssignWalker``: conditions, assignment, ``del``
* ``compile_defn``   -- ``DefnWalker``: scopes, parameters, definitions
* ``compile_stmt``   -- ``StmtWalker``: simple statements, imports, loops
* ``compile_block``  -- ``BlockWalker``: ``try``, ``with``, ``async``
* ``compile_expr``   -- ``ExprWalker``: expressions, calls, comprehensions

No method is defined twice, so the order of the chain carries no meaning; it
is a chain rather than a set of mixins only because multiple inheritance is a
build option in MicroPython (``MICROPY_MULTIPLE_INHERITANCE``) and nothing
here needs it.  Dispatch still resolves across the whole chain, because
``_build_rule_table`` looks its handlers up on the instance.
"""

from . import emitbc as E
from . import grammar as G
from . import mpy_format as MPY
from . import parsetree as PT
from . import scope as S
from . import tokens as T
from .compile_defs import (
    INVALID_LABEL, SMALL_INT_BITS, CompileError, MI_RETURN_VALUE,
    MI_LOAD_CONST_TOK, MI_LOAD_CONST_STR, MI_SET_SOURCE_LINE, MI_LOAD_NULL,
    MI_GET_ITER, MI_BUILD, W_EMIT, W_NODE, W_LOAD_ID, W_STORE_ID,
    W_LAMBDA_RETURN, W_CLASS_RETURN
)
from .compile_expr import ExprWalker

# CompileError is re-exported above: it is raised from
# ``mpyc/compile_walk.py``, but this is the module a caller that catches it
# imports.


class Compiler(ExprWalker):
    """One module's walk.  Holds the scopes, the emitter and the work stack.

    The handlers are on the chain of partial classes above; what is left here
    is the driver that walks one scope once.
    """

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
