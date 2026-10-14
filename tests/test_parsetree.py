"""Tests for the packed parse-tree arena: encoding, traversal, and memory.

Runs on CPython and unchanged on MicroPython v1.29.0, which is the point --
the memory figures that matter are the ones measured on the target runtime.

    python3 tests/test_parsetree.py
    micropython tests/test_parsetree.py
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import parsetree as pt  # noqa: E402

_fails = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def test_leaf_encoding():
    # The bit patterns documented in py/parse.h.
    check(pt.NODE_NULL == 0, "NULL is 0")

    for q in (0, 1, 7, 255, 4096, (1 << 20)):
        n = pt.new_id(q)
        check(n & 0x0F == 0x02, "id tag for q%d" % q)
        check(pt.is_id(n) and not pt.is_string(n) and not pt.is_token(n),
              "id discriminates for q%d" % q)
        check(pt.leaf_arg(n) == q, "id arg round-trips for q%d" % q)
        check(pt.is_leaf(n) and not pt.is_struct(n), "id is a leaf for q%d" % q)

        s = pt.new_string(q)
        check(s & 0x0F == 0x06, "string tag for q%d" % q)
        check(pt.is_string(s) and not pt.is_id(s), "string discriminates q%d" % q)
        check(pt.leaf_arg(s) == q, "string arg round-trips for q%d" % q)

        t = pt.new_token(q)
        check(t & 0x0F == 0x0A, "token tag for q%d" % q)
        check(pt.is_token(t) and not pt.is_id(t), "token discriminates q%d" % q)
        check(pt.leaf_arg(t) == q, "token arg round-trips for q%d" % q)
        check(pt.is_token_kind(t, q), "is_token_kind for q%d" % q)

    # Small ints are 2's complement in bits 1 and above, so the accessor is an
    # arithmetic shift and negative values must survive it.
    for v in (0, 1, -1, 2, -2, 1000, -1000,
              pt.SMALL_INT_MAX, pt.SMALL_INT_MIN):
        n = pt.new_small_int(v)
        check(n & 1 == 1, "small-int tag for %d" % v)
        check(pt.is_small_int(n), "is_small_int for %d" % v)
        check(pt.small_int_value(n) == v, "small int round-trips for %d" % v)
        check(pt.is_leaf(n) and not pt.is_struct(n), "small int is a leaf (%d)" % v)

    # The target has no bigint and a 31-bit small int; anything wider is a
    # constant, not a leaf.
    check(not pt.fits_small_int(pt.SMALL_INT_MAX + 1), "max+1 is not a small int")
    check(not pt.fits_small_int(pt.SMALL_INT_MIN - 1), "min-1 is not a small int")
    raised = False
    try:
        pt.new_small_int(pt.SMALL_INT_MAX + 1)
    except pt.ParseTreeError:
        raised = True
    check(raised, "new_small_int rejects an out-of-range value")

    # No leaf may ever look like NULL or like a struct reference.
    for n in (pt.new_small_int(0), pt.new_id(0), pt.new_string(0),
              pt.new_token(0)):
        check(n != pt.NODE_NULL, "leaf %d is not NULL" % n)
        check(n & 3 != 0, "leaf %d does not look like a struct" % n)


def test_struct_nodes():
    t = pt.ParseTree()
    a = t.new_struct(7, 12, [pt.new_id(3), pt.new_small_int(-5)])
    check(pt.is_struct(a), "struct reference is a struct")
    check(not pt.is_leaf(a), "struct reference is not a leaf")
    check(a & 3 == 0, "struct reference has clear low bits")
    check(a != pt.NODE_NULL, "struct reference is never NULL (slot 0 reserved)")
    check(t.struct_kind(a) == 7, "struct kind")
    check(t.source_line(a) == 12, "source line")
    check(t.num_children(a) == 2, "child count")
    check(pt.leaf_arg(t.child(a, 0)) == 3, "child 0")
    check(pt.small_int_value(t.child(a, 1)) == -5, "child 1")
    check(list(t.children(a)) == [t.child(a, 0), t.child(a, 1)],
          "children() matches child()")

    # A nested tree, and mutation as the parser reduces rules.
    b = t.new_struct(9, 13, [a, pt.NODE_NULL, pt.new_token(4)])
    check(t.num_children(b) == 3, "nested child count")
    check(t.child(b, 0) == a, "nested child is the earlier node")
    check(pt.is_null(t.child(b, 1)), "NULL child stays NULL")
    t.set_child(b, 1, pt.new_id(99))
    check(pt.leaf_arg(t.child(b, 1)) == 99, "set_child replaces a child")
    check(t.child(b, 0) == a and pt.is_token(t.child(b, 2)),
          "set_child leaves its neighbours alone")

    # Distinct nodes must not alias.
    c = t.new_struct(1, 1, [pt.NODE_NULL])
    check(c != a and c != b, "nodes have distinct references")

    # Out-of-range access is an error, not silent corruption.
    for bad in (-1, 2, 99):
        raised = False
        try:
            t.child(a, bad)
        except pt.ParseTreeError:
            raised = True
        check(raised, "child(%d) out of range raises" % bad)
    raised = False
    try:
        t.struct_kind(pt.new_id(1))
    except pt.ParseTreeError:
        raised = True
    check(raised, "treating a leaf as a struct raises")


def test_zero_children():
    t = pt.ParseTree()
    a = t.new_struct(3, 5, [])
    check(t.num_children(a) == 0, "zero-child node")
    check(list(t.children(a)) == [], "zero-child node yields nothing")
    b = t.new_struct(4, 6, [a])
    check(t.child(b, 0) == a, "zero-child node can still be referenced")


def test_walk():
    t = pt.ParseTree()
    leaf_only = t.new_struct(1, 1, [pt.new_id(1), pt.new_id(2)])
    mid = t.new_struct(2, 2, [leaf_only, pt.new_small_int(3)])
    root = t.new_struct(3, 3, [mid, pt.NODE_NULL, pt.new_token(1)])
    t.root = root
    order = list(t.walk())
    check(order == [root, mid, leaf_only],
          "walk yields parents before children: %r" % (order,))
    check(t.count_nodes() == 3, "count_nodes")
    # Walking a leaf or NULL yields nothing rather than raising.
    check(list(t.walk(pt.new_id(1))) == [], "walk of a leaf is empty")
    check(list(t.walk(pt.NODE_NULL)) == [], "walk of NULL is empty")

    # A deep chain must not cost Python frames -- 5000 levels would end any
    # recursive walk on the target, where the budget is 32.
    t2 = pt.ParseTree()
    node = t2.new_struct(1, 1, [pt.NODE_NULL])
    for i in range(5000):
        node = t2.new_struct(2, i, [node])
    t2.root = node
    check(t2.count_nodes() == 5001, "walk handles a 5001-deep chain")


def test_consts():
    t = pt.ParseTree()
    i = t.add_const(1 << 40)
    j = t.add_const(1 << 40)
    k = t.add_const("hello")
    check(i == j, "add_const interns equal values")
    check(k != i, "distinct constants get distinct indexes")
    check(t.const(i) == 1 << 40, "const round-trips")
    # 1 and 1.0 compare equal in Python but are different constants.
    a = t.add_const(1)
    b = t.add_const(1.0)
    check(a != b, "int and float constants stay distinct")


def test_memory():
    print("")
    print("memory:")
    itemsize = pt.ITEM_BYTES
    print("  arena item size      : %d bytes" % itemsize)

    t = pt.ParseTree()
    before = t.slots_used()
    t.new_struct(1, 1, [pt.NODE_NULL, pt.NODE_NULL, pt.NODE_NULL])
    slots = t.slots_used() - before
    print("  3-child struct node  : %d slots = %d bytes"
          % (slots, slots * itemsize))
    check(slots == 5, "a 3-child node is 2 header + 3 child slots")

    before = t.slots_used()
    t.new_struct(1, 1, [pt.new_id(1), pt.new_small_int(2), pt.new_token(3)])
    check(t.slots_used() - before == 5,
          "leaves cost no slots of their own")
    print("  3 leaves inside it   : 0 extra bytes (packed into the parent)")

    # Capacity under the budget.
    budget = 64 * 1024
    for nchild in (1, 2, 3):
        per = (2 + nchild) * itemsize
        print("  %d-child nodes in 64 KB: %d nodes (%d bytes each)"
              % (nchild, budget // per, per))

    # A realistic mixed tree, measured rather than projected.
    t3 = pt.ParseTree()
    n = 2000
    prev = pt.NODE_NULL
    for i in range(n):
        prev = t3.new_struct(i & 0xFF, i, [prev, pt.new_id(i & 0xFFFF)])
    t3.root = prev
    print("  %d 2-child nodes      : %d bytes live, %d allocated"
          % (n, t3.bytes_used(), t3.bytes_allocated()))
    check(t3.count_nodes() == n, "mixed tree walks completely")
    overhead = t3.bytes_allocated() - t3.bytes_used()
    check(overhead < 2 * pt._BLOCK_SLOTS * itemsize,
          "block growth wastes at most a block or two (wasted %d)" % overhead)


def main():
    test_leaf_encoding()
    test_struct_nodes()
    test_zero_children()
    test_walk()
    test_consts()
    test_memory()
    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all parse-tree checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
