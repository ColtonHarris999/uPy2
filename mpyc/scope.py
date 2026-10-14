"""Scope and identifier analysis: a port of ``py/scope.c``.

One ``Scope`` per Python block (module, class, function, lambda, each kind of
comprehension).  Each holds an ordered table of identifiers, one entry per
name the block mentions, and the compiler's first pass (``MP_PASS_SCOPE``)
decides each name's kind: local, cell, free, or one of the global flavours.

The substantive logic is closing over variables.  When a nested block reads a
name its parent owns, the name becomes ``FREE`` here and the chain of parents
up to the owner is walked: the owner's ``LOCAL`` becomes ``CELL``, and every
block in between gains a ``FREE`` entry so the value can be threaded through.

Identifier order is load-bearing: the table is kept in first-mention order,
which is what decides local slot numbers, and the compiler adds a function's
arguments before its body so they come first.  An identifier is referred to by
its index into that table, never by a reference to an object: the table is a
packed ``array('i')``, for reasons the ``Scope`` docstring gives.
"""

from array import array

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

# -- the packed identifier table -----------------------------------------
#
# Four 32-bit words per identifier.  For LOCAL the last one is the local's
# slot number; for CELL and FREE it is the number of the closed-over
# variable.

ID_QST = 0
ID_KIND = 1
ID_FLAGS = 2
ID_LOCAL_NUM = 3
ID_STRIDE = 4

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
# text.  These are the *texts*; a scope stores its name as a qstr number, so
# the compiler interns one of these through the parse's pool.
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


class Scope:
    """A Python block.

    The identifiers are held in one flat ``array('i')``, four words each --
    qstr number, kind, flags, local number -- rather than one small object
    per name.  On the target an object with four attributes was measured at
    about 270 bytes against a 64 KB heap, and a 250-line module mentions a
    couple of hundred names; packed, the same table is 16 bytes an entry.

    Lookup is a linear scan of that array, which is what ``scope_find`` in
    ``py/scope.c`` does: a scope has a handful of names, and a dictionary
    keyed on the qstr cost more than the scan saves.
    """

    def __init__(self, kind, pn, emit_options=0, simple_name=None, pool=None):
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
        # Four words per identifier, in first-mention order -- which is what
        # decides local slot numbers.
        self.ids = array('i')
        if simple_name is not None:
            # A qstr number.  SCOPE_FUNCTION and SCOPE_CLASS take their name
            # from the parse node, so the caller supplies it; that needs the
            # tree, which this module does not see.
            self.simple_name = simple_name
        elif pool is not None:
            text = SCOPE_SIMPLE_NAME.get(kind)
            if text is None:
                raise ScopeError("scope kind %d needs an explicit name" % kind)
            self.simple_name = pool.intern(text)
        else:
            raise ScopeError("a scope needs either a name or a qstr pool")
        # Set by the compiler once the code is emitted.
        self.raw_code = None
        self.raw_code_children = None

    # -- identifiers -----------------------------------------------------
    #
    # An identifier is referred to by its index, so that nothing has to hand
    # out a reference into the packed array.

    def num_ids(self):
        return len(self.ids) // ID_STRIDE

    def id_qst(self, i):
        return self.ids[i * ID_STRIDE + ID_QST]

    def id_kind(self, i):
        return self.ids[i * ID_STRIDE + ID_KIND]

    def set_id_kind(self, i, kind):
        self.ids[i * ID_STRIDE + ID_KIND] = kind

    def id_flags(self, i):
        return self.ids[i * ID_STRIDE + ID_FLAGS]

    def set_id_flags(self, i, flags):
        self.ids[i * ID_STRIDE + ID_FLAGS] = flags

    def id_local_num(self, i):
        return self.ids[i * ID_STRIDE + ID_LOCAL_NUM]

    def set_id_local_num(self, i, num):
        self.ids[i * ID_STRIDE + ID_LOCAL_NUM] = num

    def id_is_param(self, i):
        return bool(self.ids[i * ID_STRIDE + ID_FLAGS] & ID_FLAG_IS_PARAM)

    def swap_ids(self, i, j):
        """Exchange two entries, keeping first-mention order otherwise."""
        ids = self.ids
        a = i * ID_STRIDE
        b = j * ID_STRIDE
        for k in range(ID_STRIDE):
            ids[a + k], ids[b + k] = ids[b + k], ids[a + k]

    def find(self, qst):
        """The index of *qst* in this scope, or -1."""
        ids = self.ids
        i = 0
        n = len(ids)
        while i < n:
            if ids[i + ID_QST] == qst:
                return i // ID_STRIDE
            i += ID_STRIDE
        return -1

    def find_or_add_id(self, qst, kind):
        i = self.find(qst)
        if i >= 0:
            return i
        ids = self.ids
        ids.append(qst)
        ids.append(kind)
        ids.append(0)   # flags
        ids.append(0)   # local_num
        return len(ids) // ID_STRIDE - 1

    def find_global(self, qst):
        """``(scope, index)`` for *qst* in the outermost scope, or -1."""
        s = self
        while s.parent is not None:
            s = s.parent
        return s, s.find(qst)

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
                raise ScopeError("reached the outer scope closing over q%d"
                                 % qst)
            i = s.find_or_add_id(qst, ID_INFO_KIND_UNDECIDED)
            kind = s.id_kind(i)
            if kind == ID_INFO_KIND_UNDECIDED:
                # Not declared here, so mark it free and keep climbing.
                s.set_id_kind(i, ID_INFO_KIND_FREE)
            else:
                if kind == ID_INFO_KIND_LOCAL:
                    # This scope owns it: turn the local into a cell.
                    s.set_id_kind(i, ID_INFO_KIND_CELL)
                # FREE means a parent already closed over it; CELL means this
                # scope already did.  Either way we are done.
                return
            s = s.parent

    def check_to_close_over(self, i):
        """If an enclosing function owns identifier *i*, close over it."""
        if self.parent is None:
            return
        qst = self.id_qst(i)
        s = self.parent
        while s.parent is not None:
            other = s.find(qst)
            if other >= 0:
                if s.id_kind(other) in (ID_INFO_KIND_LOCAL,
                                        ID_INFO_KIND_CELL,
                                        ID_INFO_KIND_FREE):
                    self.set_id_kind(i, ID_INFO_KIND_FREE)
                    self._close_over_in_parents(qst)
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
        for i in range(self.num_ids()):
            parts.append("%s:%s%s" % (
                name(self.id_qst(i)), ID_KIND_NAMES[self.id_kind(i)],
                "(param)" if self.id_is_param(i) else ""))
        return "%s %s [%s]" % (
            SCOPE_KIND_NAMES[self.kind], name(self.simple_name),
            " ".join(parts))
