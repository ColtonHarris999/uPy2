"""Tests for scope and identifier analysis.

The closure algorithm is the substantive part, so these build scope chains by
hand and check what `check_to_close_over` does to each link.  There is no
oracle here: MicroPython's scopes never leave the compiler, so correctness is
established later by the emitter's byte-exact output.  What is checked here is
the algorithm's own invariants.

    python3 tests/test_scope.py
    micropython tests/test_scope.py
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import scope as S  # noqa: E402

_fails = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def chain(*kinds):
    """A parent-to-child chain of scopes."""
    scopes = []
    prev = None
    for k in kinds:
        sc = S.Scope(k, 0) if k in S.SCOPE_SIMPLE_NAME else S.Scope(
            k, 0, simple_name=b"f")
        sc.parent = prev
        scopes.append(sc)
        prev = sc
    return scopes


def test_kind_predicates():
    check(not S.scope_is_func_like(S.SCOPE_MODULE), "module is not func-like")
    check(not S.scope_is_func_like(S.SCOPE_CLASS), "class is not func-like")
    for k in (S.SCOPE_LAMBDA, S.SCOPE_LIST_COMP, S.SCOPE_DICT_COMP,
              S.SCOPE_SET_COMP, S.SCOPE_GEN_EXPR, S.SCOPE_FUNCTION):
        check(S.scope_is_func_like(k),
              "%s is func-like" % S.SCOPE_KIND_NAMES[k])
    for k in (S.SCOPE_LIST_COMP, S.SCOPE_DICT_COMP, S.SCOPE_SET_COMP,
              S.SCOPE_GEN_EXPR):
        check(S.scope_is_comp_like(k),
              "%s is comp-like" % S.SCOPE_KIND_NAMES[k])
    for k in (S.SCOPE_MODULE, S.SCOPE_CLASS, S.SCOPE_LAMBDA, S.SCOPE_FUNCTION):
        check(not S.scope_is_comp_like(k),
              "%s is not comp-like" % S.SCOPE_KIND_NAMES[k])


def test_simple_names():
    check(S.Scope(S.SCOPE_MODULE, 0).simple_name == b"<module>", "module name")
    check(S.Scope(S.SCOPE_GEN_EXPR, 0).simple_name == b"<genexpr>",
          "genexpr name")
    check(S.Scope(S.SCOPE_FUNCTION, 0, simple_name=b"foo").simple_name == b"foo",
          "an explicit name is kept")
    raised = False
    try:
        S.Scope(S.SCOPE_FUNCTION, 0)
    except S.ScopeError:
        raised = True
    check(raised, "a function scope demands a name")


def test_id_order_and_lookup():
    sc = S.Scope(S.SCOPE_FUNCTION, 0, simple_name=b"f")
    a = sc.find_or_add_id(10, S.ID_INFO_KIND_LOCAL)
    b = sc.find_or_add_id(11, S.ID_INFO_KIND_LOCAL)
    again = sc.find_or_add_id(10, S.ID_INFO_KIND_GLOBAL_EXPLICIT)
    check(again is a, "find_or_add_id returns the existing entry")
    check(again.kind == S.ID_INFO_KIND_LOCAL,
          "an existing entry keeps its kind")
    check([i.qst for i in sc.id_info] == [10, 11],
          "ids stay in first-mention order, which sets local numbers")
    check(sc.find(11) is b, "find locates an id")
    check(sc.find(99) is None, "find returns None for an unknown id")


def test_find_global():
    mod, fn, inner = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION, S.SCOPE_FUNCTION)
    g = mod.find_or_add_id(7, S.ID_INFO_KIND_GLOBAL_IMPLICIT)
    check(inner.find_global(7) is g, "find_global reaches the outermost scope")
    check(inner.find_global(8) is None, "find_global misses an unknown name")


def test_close_over_one_level():
    # def outer():      x = 1        -> x becomes CELL
    #     def inner():  return x     -> x becomes FREE
    mod, outer, inner = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION,
                              S.SCOPE_FUNCTION)
    x_outer = outer.find_or_add_id(20, S.ID_INFO_KIND_LOCAL)
    x_inner = inner.find_or_add_id(20, S.ID_INFO_KIND_UNDECIDED)
    inner.check_to_close_over(x_inner)
    check(x_inner.kind == S.ID_INFO_KIND_FREE, "the inner reference is FREE")
    check(x_outer.kind == S.ID_INFO_KIND_CELL, "the owner's local becomes CELL")


def test_close_over_two_levels():
    # The middle scope never mentions x, but must still gain a FREE entry so
    # the value can be threaded through it.
    mod, a, b, c = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION, S.SCOPE_FUNCTION,
                         S.SCOPE_FUNCTION)
    x_a = a.find_or_add_id(21, S.ID_INFO_KIND_LOCAL)
    x_c = c.find_or_add_id(21, S.ID_INFO_KIND_UNDECIDED)
    c.check_to_close_over(x_c)
    check(x_c.kind == S.ID_INFO_KIND_FREE, "the deepest reference is FREE")
    check(x_a.kind == S.ID_INFO_KIND_CELL, "the owner becomes CELL")
    mid = b.find(21)
    check(mid is not None, "the middle scope gained an entry for the name")
    if mid is not None:
        check(mid.kind == S.ID_INFO_KIND_FREE,
              "the middle scope's entry is FREE, to thread the value through")


def test_no_close_over_when_global():
    # A module-level name is global, not free: the walk stops before the
    # outermost scope, so nothing is closed over.
    mod, fn = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION)
    mod.find_or_add_id(22, S.ID_INFO_KIND_LOCAL)
    x = fn.find_or_add_id(22, S.ID_INFO_KIND_UNDECIDED)
    fn.check_to_close_over(x)
    check(x.kind == S.ID_INFO_KIND_UNDECIDED,
          "a module-level name is not closed over")


def test_no_close_over_without_owner():
    mod, a, b = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION, S.SCOPE_FUNCTION)
    x = b.find_or_add_id(23, S.ID_INFO_KIND_UNDECIDED)
    b.check_to_close_over(x)
    check(x.kind == S.ID_INFO_KIND_UNDECIDED,
          "a name no parent owns stays undecided")
    check(a.find(23) is None, "and no parent gains a spurious entry")


def test_close_over_idempotent():
    mod, a, b, c = chain(S.SCOPE_MODULE, S.SCOPE_FUNCTION, S.SCOPE_FUNCTION,
                         S.SCOPE_FUNCTION)
    a.find_or_add_id(24, S.ID_INFO_KIND_LOCAL)
    x_b = b.find_or_add_id(24, S.ID_INFO_KIND_UNDECIDED)
    b.check_to_close_over(x_b)
    x_c = c.find_or_add_id(24, S.ID_INFO_KIND_UNDECIDED)
    c.check_to_close_over(x_c)
    check(a.find(24).kind == S.ID_INFO_KIND_CELL, "the owner stays CELL")
    check(b.find(24).kind == S.ID_INFO_KIND_FREE, "the middle stays FREE")
    check(x_c.kind == S.ID_INFO_KIND_FREE, "the second closure is also FREE")
    check(len(b.id_info) == 1, "no duplicate entry was added")


def test_deep_chain_needs_no_frames():
    # 400 nested scopes: the walk is iterative, so depth costs no Python
    # frames on a machine with a 32-frame budget.
    kinds = [S.SCOPE_MODULE] + [S.SCOPE_FUNCTION] * 400
    scopes = chain(*kinds)
    scopes[1].find_or_add_id(25, S.ID_INFO_KIND_LOCAL)
    deep = scopes[-1].find_or_add_id(25, S.ID_INFO_KIND_UNDECIDED)
    scopes[-1].check_to_close_over(deep)
    check(deep.kind == S.ID_INFO_KIND_FREE, "a 400-deep closure resolves")
    check(scopes[1].find(25).kind == S.ID_INFO_KIND_CELL,
          "the distant owner becomes CELL")
    threaded = 0
    for sc in scopes[2:-1]:
        info = sc.find(25)
        if info is not None and info.kind == S.ID_INFO_KIND_FREE:
            threaded += 1
    check(threaded == len(scopes) - 3,
          "every scope in between threads it through (%d)" % threaded)


def main():
    test_kind_predicates()
    test_simple_names()
    test_id_order_and_lookup()
    test_find_global()
    test_close_over_one_level()
    test_close_over_two_levels()
    test_no_close_over_when_global()
    test_no_close_over_without_owner()
    test_close_over_idempotent()
    test_deep_chain_needs_no_frames()
    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all scope checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
