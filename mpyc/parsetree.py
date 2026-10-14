"""The parse tree, packed into a flat ``array('i')`` arena.

MicroPython stores a parse node in a single machine word (``py/parse.h``):

    0000...0000   no node
    xxxx...xxx1   small integer; bits 1+ are the signed value, 2's complement
    xxxx...xx00   pointer to mp_parse_node_struct_t
    xx...xx0010   identifier; bits 4+ are the qstr
    xx...xx0110   string; bits 4+ are the qstr holding the value
    xx...xx1010   token; bits 4+ are the token kind

and a struct node as two ``uint32_t`` of header followed by its children::

    struct { uint32_t source_line; uint32_t kind_num_nodes; mp_parse_node_t nodes[]; }

with the kind in the low 8 bits of ``kind_num_nodes`` and the child count above
them.

This module reproduces that layout exactly, with one substitution: where C
holds a *pointer* to a struct node, we hold an *index* into one flat
``array('i')``.  The tagging is unchanged, so a struct reference is
``slot << 2`` -- low two bits clear, as C requires -- and arena slot 0 is
reserved so that a reference is never confused with ``NODE_NULL``.

Why this and not Python objects: the budget is 64 KB of heap for a 200-line
module, and the parse tree is the dominant consumer because the four compiler
passes (``MP_PASS_SCOPE``, ``STACK_SIZE``, ``CODE_SIZE``, ``EMIT``) all run
over it before it can be freed.  In the arena a struct node of three children
costs 5 slots -- 20 bytes, the same as the C compiler on a 32-bit target --
and a leaf costs *nothing extra*, because it lives inside its parent's child
slot exactly as in C.  A tree of Python objects pays a header per node and
boxes every leaf.

``array('i')`` is 32 bits wide, which is also the target's word size, so the
small-int range this arena can hold inline is the target's own: a value
needing more than 31 bits does not fit ``(value << 1) | 1`` and belongs in
the constant table, which is precisely the rule the target enforces.

No recursion anywhere: traversal runs off an explicit stack, because the
target bans it.
"""

from array import array
from struct import calcsize, pack

# MicroPython's array has no .itemsize, so measure the width of one slot once.
# 'i' is a C int: 4 bytes on every platform MicroPython targets, and the same
# width as the target's word.
ITEM_BYTES = calcsize('i')

# -- node tags (py/parse.h) ------------------------------------------------

NODE_NULL = 0

TAG_SMALL_INT = 0x01   # tested with & 0x01
TAG_ID = 0x02          # tested with & 0x0F
TAG_STRING = 0x06
TAG_TOKEN = 0x0A

# Header words of a struct node, relative to its slot.
_OFF_SOURCE_LINE = 0
_OFF_KIND_NUM = 1
_HEADER_SLOTS = 2

# The arena grows by whole blocks rather than doubling: at a 64 KB ceiling a
# doubling realloc would need the old and new buffers live at once, which can
# cost more than the tree itself.
_BLOCK_SLOTS = 256
_BLANK = array('i', [0] * 32)

# The widest value that fits a small-int leaf, matching the target's 31-bit
# small int: (value << 1) | 1 must stay inside a signed 32-bit word.
SMALL_INT_MIN = -(1 << 30)
SMALL_INT_MAX = (1 << 30) - 1


class ParseTreeError(Exception):
    pass


def fits_small_int(value):
    """True if *value* can be a small-int leaf rather than a constant."""
    return SMALL_INT_MIN <= value <= SMALL_INT_MAX


# -- leaf constructors and accessors --------------------------------------
#
# Leaves are plain integers; they occupy no arena slot of their own.

def new_small_int(value):
    if not fits_small_int(value):
        raise ParseTreeError("value %d does not fit a small-int leaf" % value)
    return (value << 1) | TAG_SMALL_INT


def new_id(qstr):
    return (qstr << 4) | TAG_ID


def new_string(qstr):
    return (qstr << 4) | TAG_STRING


def new_token(token_kind):
    return (token_kind << 4) | TAG_TOKEN


def is_null(node):
    return node == NODE_NULL


def is_leaf(node):
    return (node & 3) != 0


def is_struct(node):
    return node != NODE_NULL and (node & 3) == 0


def is_small_int(node):
    return (node & 1) == TAG_SMALL_INT


def is_id(node):
    return (node & 0x0F) == TAG_ID


def is_string(node):
    return (node & 0x0F) == TAG_STRING


def is_token(node):
    return (node & 0x0F) == TAG_TOKEN


def is_token_kind(node, token_kind):
    return node == ((token_kind << 4) | TAG_TOKEN)


def leaf_kind(node):
    return node & 0x0F


def leaf_arg(node):
    """The qstr or token kind carried by a non-small-int leaf."""
    return node >> 4


def small_int_value(node):
    """The signed value of a small-int leaf (an arithmetic shift, as in C)."""
    return node >> 1


class MpStr:
    """A MicroPython ``str`` constant.

    MicroPython strings are byte sequences, 8-bit clean and not necessarily
    valid UTF-8 -- a literal may contain lone surrogate escapes.  Decoding to
    a Python str would not round-trip those, so the bytes are kept as they
    are and only the type is remembered, which also keeps a str constant
    distinct from a bytes constant.
    """

    def __init__(self, data):
        self.data = data

    def __eq__(self, other):
        return isinstance(other, MpStr) and other.data == self.data

    def __hash__(self):
        return hash(self.data)

    def __bool__(self):
        return len(self.data) != 0

    # MicroPython has no __bool__; it uses __len__ for truthiness.
    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return "MpStr(%r)" % (self.data,)


def _scalar_key(value):
    t = type(value)
    if t is bool:
        return ("b", value)
    if t is int:
        return ("i", value)
    if t is float:
        # The raw bits, not repr: repr cannot tell 0.0 from -0.0 or a NaN
        # from a negative NaN, and all three are distinct constants.
        return ("f", pack("<d", value))
    if t is MpStr:
        return ("S", value.data)
    if t is str:
        return ("s", value)
    if t is bytes:
        return ("y", value)
    if t is complex:
        return ("c", pack("<d", value.real), pack("<d", value.imag))
    if value is None:
        return ("n",)
    if value is Ellipsis:
        return ("e",)
    return ("o", id(value))


def const_key(value):
    """A hashable key that distinguishes constants Python would equate.

    ``1 == 1.0`` and ``0.0 == -0.0``, and both pairs also hash alike, so the
    key carries the type, and for floats the raw bits.

    A tuple is flattened into prefix form with explicit lengths rather than
    keyed recursively: tuple nesting follows the source, so recursing here
    would put an input-dependent depth on a 32-frame stack.
    """
    out = []
    stack = [value]
    while stack:
        v = stack.pop()
        if type(v) is tuple:
            out.append(("t", len(v)))
            i = len(v) - 1
            while i >= 0:
                stack.append(v[i])
                i -= 1
            continue
        out.append(_scalar_key(v))
    return tuple(out)


class ParseTree:
    """A parse tree held in one flat arena.

    Struct nodes live in ``self.arena`` (an ``array('i')``); ``self.root`` is
    the reference to the outermost node.  Constants too wide for a small-int
    leaf live in ``self.consts`` and are referred to by index, mirroring the
    way C stores an ``mp_obj_t`` in ``nodes[0]`` of a const-object node --
    that slot is not a parse node in either implementation.
    """

    def __init__(self, capacity_slots=_BLOCK_SLOTS):
        self.arena = array('i')
        # Slot 0 is reserved: a struct reference is slot << 2, so a node at
        # slot 0 would encode as 0 and collide with NODE_NULL.
        self._grow_to(max(1, capacity_slots))
        self.used = 1
        self.root = NODE_NULL
        self.consts = []
        self._const_index = {}

    # -- arena management -------------------------------------------------

    def _grow_to(self, slots):
        a = self.arena
        blank = _BLANK
        n = len(blank)
        while len(a) < slots:
            a.extend(blank)
            if len(a) + n > slots and len(a) < slots:
                # Top up with single slots so capacity lands exactly.
                while len(a) < slots:
                    a.append(0)
        return a

    def reserve(self, slots):
        """Make room for *slots* more slots, growing by whole blocks."""
        need = self.used + slots
        if need <= len(self.arena):
            return
        target = len(self.arena)
        while target < need:
            target += _BLOCK_SLOTS
        self._grow_to(target)

    def slots_used(self):
        return self.used

    def slots_allocated(self):
        return len(self.arena)

    def bytes_used(self):
        """Live arena payload, in bytes -- what the budget is measured in."""
        return self.used * ITEM_BYTES

    def bytes_allocated(self):
        return len(self.arena) * ITEM_BYTES

    # -- struct nodes -----------------------------------------------------

    def new_struct(self, kind, source_line, children):
        """Append a struct node and return a reference to it.

        *children* are node values (leaves, references, or ``NODE_NULL``).
        """
        if kind < 0 or kind > 0xFF:
            raise ParseTreeError("struct kind %d out of range" % kind)
        n = len(children)
        self.reserve(_HEADER_SLOTS + n)
        slot = self.used
        a = self.arena
        a[slot + _OFF_SOURCE_LINE] = source_line
        a[slot + _OFF_KIND_NUM] = kind | (n << 8)
        i = 0
        base = slot + _HEADER_SLOTS
        while i < n:
            a[base + i] = children[i]
            i += 1
        self.used = slot + _HEADER_SLOTS + n
        return slot << 2

    def _slot(self, node):
        if node == NODE_NULL or (node & 3) != 0:
            raise ParseTreeError("node %d is not a struct reference" % node)
        return node >> 2

    def source_line(self, node):
        return self.arena[self._slot(node) + _OFF_SOURCE_LINE]

    def struct_kind(self, node):
        return self.arena[self._slot(node) + _OFF_KIND_NUM] & 0xFF

    def num_children(self, node):
        return self.arena[self._slot(node) + _OFF_KIND_NUM] >> 8

    def child(self, node, i):
        slot = self._slot(node)
        n = self.arena[slot + _OFF_KIND_NUM] >> 8
        if i < 0 or i >= n:
            raise ParseTreeError("child %d out of range (node has %d)" % (i, n))
        return self.arena[slot + _HEADER_SLOTS + i]

    def set_child(self, node, i, value):
        """Replace a child.  The parser rewrites children as rules reduce."""
        slot = self._slot(node)
        n = self.arena[slot + _OFF_KIND_NUM] >> 8
        if i < 0 or i >= n:
            raise ParseTreeError("child %d out of range (node has %d)" % (i, n))
        self.arena[slot + _HEADER_SLOTS + i] = value

    def children(self, node):
        """Yield each child of a struct node."""
        slot = self._slot(node)
        n = self.arena[slot + _OFF_KIND_NUM] >> 8
        base = slot + _HEADER_SLOTS
        i = 0
        while i < n:
            yield self.arena[base + i]
            i += 1

    # -- constants --------------------------------------------------------

    def add_const(self, value):
        """Intern *value* in the constant table and return its index.

        Keyed on an exact key rather than ``==``: 1 and 1.0 compare equal in
        Python, and so do 0.0 and -0.0, but they are different constants.
        """
        key = const_key(value)
        i = self._const_index.get(key)
        if i is not None:
            return i
        i = len(self.consts)
        self.consts.append(value)
        self._const_index[key] = i
        return i

    def const(self, index):
        return self.consts[index]

    # -- traversal --------------------------------------------------------

    def walk(self, node=None):
        """Yield every struct reference in the tree, parents before children.

        Runs off an explicit stack held in the heap; nothing here recurses.
        """
        if node is None:
            node = self.root
        if not is_struct(node):
            return
        stack = [node]
        a = self.arena
        while stack:
            cur = stack.pop()
            yield cur
            slot = cur >> 2
            n = a[slot + _OFF_KIND_NUM] >> 8
            base = slot + _HEADER_SLOTS
            # Push in reverse so children come out left to right.
            i = n - 1
            while i >= 0:
                ch = a[base + i]
                if ch != NODE_NULL and (ch & 3) == 0:
                    stack.append(ch)
                i -= 1

    def count_nodes(self, node=None):
        """Number of struct nodes in the tree."""
        n = 0
        for _ in self.walk(node):
            n += 1
        return n

    def describe(self, node):
        """A short, human-readable form of one node, for debugging."""
        if is_null(node):
            return "NULL"
        if is_small_int(node):
            return "int(%d)" % small_int_value(node)
        if is_id(node):
            return "id(q%d)" % leaf_arg(node)
        if is_string(node):
            return "str(q%d)" % leaf_arg(node)
        if is_token(node):
            return "tok(%d)" % leaf_arg(node)
        return "struct(kind=%d, n=%d, line=%d)" % (
            self.struct_kind(node), self.num_children(node),
            self.source_line(node))
