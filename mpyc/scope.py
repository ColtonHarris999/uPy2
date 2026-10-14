"""Scope and identifier analysis: a port of ``py/scope.c``.

One ``Scope`` per Python block (module, class, function, lambda, each kind of
comprehension).  Each holds an ordered list of ``IdInfo``, one per name the
block mentions, and the compiler's first pass (``MP_PASS_SCOPE``) decides each
name's kind: local, cell, free, or one of the global flavours.

The substantive logic is closing over variables.  When a nested block reads a
name its parent owns, the name becomes ``FREE`` here and the chain of parents
up to the owner is walked: the owner's ``LOCAL`` becomes ``CELL``, and every
block in between gains a ``FREE`` entry so the value can be threaded through.

Identifier order is load-bearing: ``id_info`` is kept in first-mention order,
which is what decides local slot numbers, and the compiler adds a function's
arguments before its body so they come first.
"""

from . import qstrs

# -- id kinds (scope.h) ---------------------------------------------------

ID_INFO_KIND_UNDECIDED = 0
ID_INFO_KIND_GLOBAL_IMPLICIT = 1
ID_INFO_KIND_GLOBAL_IMPLICIT_ASSIGNED = 2
ID_INFO_KIND_GLOBAL_EXPLICIT = 3
ID_INFO_KIND_LOCAL = 4   # written and referenced only by this function
ID_INFO_KIND_CELL = 5    # read or written by children of this function
ID_INFO_KIND_FREE = 6    # belongs to a parent of this function

ID_KIND_NAMES = (
    "UNDECIDED", "GLOBAL_IMPLICIT", "GLOBAL_IMPLICIT_ASSIGNED",
    "GLOBAL_EXPLICIT", "LOCAL", "CELL", "FREE",
)

# -- id flags -------------------------------------------------------------

ID_FLAG_IS_PARAM = 0x01
ID_FLAG_IS_STAR_PARAM = 0x02
ID_FLAG_IS_DBL_STAR_PARAM = 0x04
ID_FLAG_VIPER_TYPE_POS = 4

# -- scope kinds ----------------------------------------------------------
#
# The order matters: SCOPE_IS_FUNC_LIKE is ">= LAMBDA" and SCOPE_IS_COMP_LIKE
# is the LIST_COMP..GEN_EXPR range, both as range tests in C.

SCOPE_MODULE = 0
SCOPE_CLASS = 1
SCOPE_LAMBDA = 2
SCOPE_LIST_COMP = 3
SCOPE_DICT_COMP = 4
SCOPE_SET_COMP = 5
SCOPE_GEN_EXPR = 6
SCOPE_FUNCTION = 7

SCOPE_KIND_NAMES = ("MODULE", "CLASS", "LAMBDA", "LIST_COMP", "DICT_COMP",
                    "SET_COMP", "GEN_EXPR", "FUNCTION")


def scope_is_func_like(kind):
    return kind >= SCOPE_LAMBDA


def scope_is_comp_like(kind):
    return SCOPE_LIST_COMP <= kind <= SCOPE_GEN_EXPR


# -- scope flags (runtime0.h) --------------------------------------------

MP_SCOPE_FLAG_ALL_SIG = 0x0F
MP_SCOPE_FLAG_GENERATOR = 0x01
MP_SCOPE_FLAG_VARKEYWORDS = 0x02
MP_SCOPE_FLAG_VARARGS = 0x04
MP_SCOPE_FLAG_DEFKWARGS = 0x08
MP_SCOPE_FLAG_REFGLOBALS = 0x10
MP_SCOPE_FLAG_HASCONSTS = 0x20

# The implicit name of each scope kind.  Only "<module>" is in the static qstr
# pool; the others live in the build's pool and so are written to an .mpy as
# text.  Stored as bytes, like every other qstr here.
SCOPE_SIMPLE_NAME = {
    SCOPE_MODULE: b"<module>",
    SCOPE_LAMBDA: b"<lambda>",
    SCOPE_LIST_COMP: b"<listcomp>",
    SCOPE_DICT_COMP: b"<dictcomp>",
    SCOPE_SET_COMP: b"<setcomp>",
    SCOPE_GEN_EXPR: b"<genexpr>",
}


class ScopeError(Exception):
    pass


class IdInfo:
    """One name in one scope."""

    def __init__(self, qst, kind):
        self.qst = qst          # qstr number
        self.kind = kind
        self.flags = 0
        # For LOCAL this is the local's slot number; for CELL and FREE it is
        # the number of the closed-over variable.
        self.local_num = 0

    def is_param(self):
        return bool(self.flags & ID_FLAG_IS_PARAM)

    def __repr__(self):
        return "IdInfo(q%d, %s, flags=%#x, local=%d)" % (
            self.qst, ID_KIND_NAMES[self.kind], self.flags, self.local_num)


class Scope:
    """A Python block."""

    def __init__(self, kind, pn, emit_options=0, simple_name=None):
        self.kind = kind
        self.parent = None
        self.next = None
        self.pn = pn            # the parse node this scope came from
        self.emit_options = emit_options
        self.scope_flags = 0
        self.num_pos_args = 0
        self.num_kwonly_args = 0
        self.num_def_pos_args = 0
        self.num_locals = 0
        self.stack_size = 0
        self.exc_stack_size = 0
        # First-mention order, which is what decides local slot numbers.
        self.id_info = []
        self._by_qstr = {}
        if simple_name is not None:
            # SCOPE_FUNCTION and SCOPE_CLASS take their name from the parse
            # node; the caller supplies it since that needs the tree.
            self.simple_name = simple_name
        else:
            name = SCOPE_SIMPLE_NAME.get(kind)
            if name is None:
                raise ScopeError("scope kind %d needs an explicit name" % kind)
            self.simple_name = name

    # -- identifiers -----------------------------------------------------

    def find(self, qst):
        """The IdInfo for *qst* in this scope, or None."""
        return self._by_qstr.get(qst)

    def find_or_add_id(self, qst, kind):
        info = self._by_qstr.get(qst)
        if info is not None:
            return info
        info = IdInfo(qst, kind)
        self.id_info.append(info)
        self._by_qstr[qst] = info
        return info

    def find_global(self, qst):
        """Look *qst* up in the outermost scope."""
        s = self
        while s.parent is not None:
            s = s.parent
        return s.find(qst)

    # -- closing over ----------------------------------------------------

    def _close_over_in_parents(self, qst):
        """Thread *qst* up to the scope that owns it.

        Every scope between here and the owner gains a FREE entry; the owner's
        LOCAL becomes CELL.  Walks the chain iteratively -- nesting depth
        follows the source.
        """
        if self.parent is None:
            raise ScopeError("close over with no parent scope")
        s = self.parent
        while True:
            if s.parent is None:
                # The outer scope should never be reached: a name owned by the
                # module is global, not free.
                raise ScopeError("reached the outer scope closing over q%d" % qst)
            info = s.find_or_add_id(qst, ID_INFO_KIND_UNDECIDED)
            if info.kind == ID_INFO_KIND_UNDECIDED:
                # Not declared here, so mark it free and keep climbing.
                info.kind = ID_INFO_KIND_FREE
            else:
                if info.kind == ID_INFO_KIND_LOCAL:
                    # This scope owns it: turn the local into a cell.
                    info.kind = ID_INFO_KIND_CELL
                # FREE means a parent already closed over it; CELL means this
                # scope already did.  Either way we are done.
                return
            s = s.parent

    def check_to_close_over(self, info):
        """If an enclosing function owns *info*'s name, close over it."""
        if self.parent is None:
            return
        s = self.parent
        while s.parent is not None:
            other = s.find(info.qst)
            if other is not None:
                if other.kind in (ID_INFO_KIND_LOCAL, ID_INFO_KIND_CELL,
                                  ID_INFO_KIND_FREE):
                    info.kind = ID_INFO_KIND_FREE
                    self._close_over_in_parents(info.qst)
                break
            s = s.parent

    # -- diagnostics -----------------------------------------------------

    def describe(self, pool=None):
        def name(q):
            if pool is not None:
                return pool.text(q).decode("utf-8", "replace")
            if q < qstrs.STATIC_POOL_SIZE:
                return qstrs.static_text(q)
            return "q%d" % q
        parts = []
        for info in self.id_info:
            parts.append("%s:%s%s" % (
                name(info.qst), ID_KIND_NAMES[info.kind],
                "(param)" if info.is_param() else ""))
        return "%s %s [%s]" % (
            SCOPE_KIND_NAMES[self.kind],
            self.simple_name.decode("utf-8", "replace"),
            " ".join(parts))
