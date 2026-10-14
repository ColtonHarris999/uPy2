"""Reading a constant out of a parse node.

``mp_parse_node_is_const`` and friends live in ``py/parse.c`` and are used by
both halves of the front end: the parser folds constants with them, and the
compiler uses them for the ``if False:`` and ``while True:`` optimisations and
to decide that a lonely constant statement emits nothing.  They need the arena
(to read a ``const_object`` node's value) and the interning pool (to read a
string leaf's text), so they take both rather than living on either class.

``const_value`` returns a *one-tuple* so that a constant whose value is
``None`` or ``False`` is distinguishable from "not a constant" -- C has
``MP_OBJ_NULL`` for that and Python does not.
"""

from . import grammar as G
from . import parsetree as PT
from . import tokens as T


def const_value(tree, pool, pn):
    """``(value,)`` if *pn* is a constant, else None."""
    if PT.is_small_int(pn):
        return (PT.small_int_value(pn),)
    if PT.is_struct(pn) and tree.struct_kind(pn) == G.RULE_const_object:
        return (tree.const(tree.child(pn, 0)),)
    if PT.is_token(pn):
        arg = PT.leaf_arg(pn)
        if arg == T.MP_TOKEN_KW_NONE:
            return (None,)
        if arg == T.MP_TOKEN_KW_TRUE:
            return (True,)
        if arg == T.MP_TOKEN_KW_FALSE:
            return (False,)
        if arg == T.MP_TOKEN_ELLIPSIS:
            return (Ellipsis,)
    if PT.is_string(pn):
        return (PT.MpStr(pool.text(PT.leaf_arg(pn))),)
    if PT.is_struct(pn) and tree.struct_kind(pn) == G.RULE_atom_paren:
        # `()` survives as atom_paren with a NULL child, and counts as the
        # constant empty tuple (mp_parse_node_is_const).
        if PT.is_null(tree.child(pn, 0)):
            return ((),)
    return None


def is_const(tree, pool, pn):
    return const_value(tree, pool, pn) is not None


def number_value(tree, pool, pn):
    """mp_parse_node_get_number_maybe: ``(int|float,)``, else None."""
    v = const_value(tree, pool, pn)
    if v is None:
        return None
    if type(v[0]) is int or type(v[0]) is float:
        return v
    return None


def is_const_bool(tree, pool, pn, value):
    """parse_node_is_const_bool: a constant whose truth equals *value*."""
    v = const_value(tree, pool, pn)
    if v is None:
        return False
    return bool(v[0]) == value
