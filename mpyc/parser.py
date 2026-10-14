"""The parser: a port of ``py/parse.c`` from MicroPython v1.29.0.

A table-driven LL parser over an explicit rule stack and result stack, not
recursive descent -- which is what makes it portable to a machine with a
32-frame budget.  ``mpyc/grammar.py`` holds the tables; this is the engine
that walks them and builds nodes in the ``array('i')`` arena.

Three things it does besides parsing, all of which shape the tree the
compiler later reads:

* **Node folding.**  A rule marked ``and_ident`` whose match produced a single
  non-null argument emits no node at all; the argument stands in its place.
  Several rules (``atom_paren``, ``testlist_comp``) have bespoke
  simplifications on top.
* **Constant folding.**  ``1 + 2`` leaves no ``arith_expr`` node, just the
  value, and ``MICROPY_COMP_CONST_FOLDING`` covers arithmetic, logical
  short-circuiting and ``not``.  Const tuples fold to a single object.
  ``N = const(4)`` records a value the parser then substitutes at use sites.
* **Docstring discarding.**  With ``MICROPY_ENABLE_DOC_STRING`` off -- which
  is how mpy-cross is built -- a lonely constant statement is replaced by
  ``pass``.

Not implemented: ``MICROPY_COMP_MODULE_CONST`` (folding ``errno.ENOENT`` and
similar).  The module table that feature consults is empty in mpy-cross, so
the path never fires there; confirmed by probing the oracle.
"""

from . import grammar as G
from . import parsetree as PT
from . import qstrs
from . import tokens as T
from .lexer import Lexer

# py/mpconfig.h: strings up to this length are interned, longer ones only get
# a leaf if an equal string happens to be interned already.
ALLOC_PARSE_INTERN_STRING_LEN = 10

# Input kinds (mp_parse_input_kind_t).
PARSE_SINGLE_INPUT = 0
PARSE_FILE_INPUT = 1
PARSE_EVAL_INPUT = 2


class ParseError(Exception):
    """A syntax error, carrying the kind the C parser would raise."""

    def __init__(self, kind, msg, line):
        Exception.__init__(self, msg)
        self.kind = kind  # "SyntaxError" or "IndentationError"
        self.msg = msg
        self.line = line

    def __str__(self):
        return "%s: %s (line %d)" % (self.kind, self.msg, self.line)


class QstrPool:
    """Interned strings for one parse.

    Static qstrs keep their fixed numbers; anything else is numbered after the
    static pool.  The numbers are internal -- an ``.mpy`` writes a static
    index or the text itself -- but identity has to be right, because whether
    a string literal becomes a leaf or a constant depends on whether it is
    interned.
    """

    def __init__(self, preloaded=None):
        self._dynamic = {}
        self._texts = []
        # Strings the compiling build already has interned.  They are not
        # numbered here -- only membership matters -- but they decide whether
        # a long string literal becomes a qstr leaf or a constant object.
        if preloaded is None:
            preloaded = _default_preloaded()
        self._preloaded = preloaded
        # The static pool keyed by bytes: qstrs are byte sequences here, as
        # they are in MicroPython.  Every static entry is ASCII.
        self._static = {}
        for i in range(1, qstrs.STATIC_POOL_SIZE):
            self._static[qstrs.static_text(i).encode("utf-8")] = i

    def intern(self, raw):
        """*raw* is bytes.  Returns its qstr number, assigning one if new."""
        n = self._static.get(raw)
        if n is not None:
            return n
        n = self._dynamic.get(raw)
        if n is not None:
            return n
        n = qstrs.STATIC_POOL_SIZE + len(self._texts)
        self._texts.append(raw)
        self._dynamic[raw] = n
        return n

    def find(self, raw):
        """The number for *raw* if already interned, else None."""
        n = self._static.get(raw)
        if n is not None:
            return n
        n = self._dynamic.get(raw)
        if n is not None:
            return n
        if raw in self._preloaded:
            # Already in the build's pool: interning it now gives it a number.
            return self.intern_new(raw)
        return None

    def intern_new(self, raw):
        n = qstrs.STATIC_POOL_SIZE + len(self._texts)
        self._texts.append(raw)
        self._dynamic[raw] = n
        return n

    def text(self, number):
        """The qstr's bytes."""
        if number < qstrs.STATIC_POOL_SIZE:
            return qstrs.static_text(number).encode("utf-8")
        return self._texts[number - qstrs.STATIC_POOL_SIZE]


# Token -> the binary operation it folds with.  py/lexer.h states that the 13
# tokens from OP_PIPE to OP_DBL_STAR match mp_binary_op_t in order; this spells
# the mapping out rather than relying on that arithmetic.
def _op_or(a, b):
    return a | b


def _op_xor(a, b):
    return a ^ b


def _op_and(a, b):
    return a & b


def _op_lshift(a, b):
    return a << b


def _op_rshift(a, b):
    return a >> b


def _op_add(a, b):
    return a + b


def _op_sub(a, b):
    return a - b


def _op_mul(a, b):
    return a * b


def _op_floordiv(a, b):
    return a // b


def _op_truediv(a, b):
    return a / b


def _op_mod(a, b):
    return a % b


def _op_pow(a, b):
    return a ** b


_BINOPS = {
    T.MP_TOKEN_OP_PIPE: _op_or,
    T.MP_TOKEN_OP_CARET: _op_xor,
    T.MP_TOKEN_OP_AMPERSAND: _op_and,
    T.MP_TOKEN_OP_DBL_LESS: _op_lshift,
    T.MP_TOKEN_OP_DBL_MORE: _op_rshift,
    T.MP_TOKEN_OP_PLUS: _op_add,
    T.MP_TOKEN_OP_MINUS: _op_sub,
    T.MP_TOKEN_OP_STAR: _op_mul,
    T.MP_TOKEN_OP_DBL_SLASH: _op_floordiv,
    T.MP_TOKEN_OP_SLASH: _op_truediv,
    T.MP_TOKEN_OP_PERCENT: _op_mod,
    T.MP_TOKEN_OP_DBL_STAR: _op_pow,
    # MP_TOKEN_OP_AT (matrix multiply) has no constant-foldable meaning.
}

# Rules whose binary operator is implied by the rule rather than a token.
_RULE_IMPLIED_OP = {
    G.RULE_expr: _op_or,
    G.RULE_xor_expr: _op_xor,
    G.RULE_and_expr: _op_and,
    G.RULE_power: _op_pow,
}


# MICROPY_COMP_MODULE_CONST's table, restricted to what the pinned mpy-cross
# links: py/modmath.c's int/float attributes.  MICROPY_PY_ERRNO and
# MICROPY_PY_UCTYPES are not in that build, verified against the oracle.
_INF = float("inf")
# math.nan is a positive NaN; -math.nan folds to a negative one, and the
# sign bit has to survive, so the test compares it explicitly.
_NAN = float("nan")


_PRELOADED_CACHE = []


def _default_preloaded():
    """The target firmware's qstr pool, loaded once.

    This is the *device's* pool, not mpy-cross's, because this compiler runs
    on the device: a string literal's leaf-or-constant fate must be decided
    by the pool the generated bytecode will actually run against.

    One consequence, deliberately accepted: mpy-cross's pool is smaller, so
    for a long string literal present in one pool and not the other, mpy-cross
    and this compiler emit different bytecode.  mpy-cross therefore is not a
    byte-exact oracle for those files; the differential tests pass it its own
    pool so they keep checking the engine rather than the pool.  Regenerate
    this file from your firmware's build with tools/gen_qstr_pool.py.
    """
    if not _PRELOADED_CACHE:
        try:
            from .qstr_pool import PRELOADED
        except ImportError:
            PRELOADED = frozenset()
        encoded = set()
        for t in PRELOADED:
            encoded.add(t.encode("utf-8"))
        _PRELOADED_CACHE.append(encoded)
    return _PRELOADED_CACHE[0]

MODULE_CONSTANTS = {
    b"math": {
        b"e": 2.718281828459045,
        b"pi": 3.141592653589793,
        b"tau": 6.283185307179586,
        b"inf": _INF,
        b"nan": _NAN,
    },
}


class Parser:
    def __init__(self, lexer, input_kind=PARSE_FILE_INPUT, pool=None):
        self.lex = lexer
        self.input_kind = input_kind
        self.pool = pool if pool is not None else QstrPool()
        self.tree = PT.ParseTree()
        # Rule stack entries are three parallel lists rather than tuples: no
        # object per entry, and the depth can reach a few hundred.
        self._rs_line = []
        self._rs_rule = []
        self._rs_arg = []
        self.result = []
        # id -> value, for `N = const(4)`
        self.consts = {}

    # -- stacks ----------------------------------------------------------

    def push_rule(self, src_line, rule_id, arg_i):
        self._rs_line.append(src_line)
        self._rs_rule.append(rule_id)
        self._rs_arg.append(arg_i)

    def push_rule_from_arg(self, arg):
        self.push_rule(self.lex.tok_line, arg & G.RULE_ARG_ARG_MASK, 0)

    def pop_rule(self):
        return (self._rs_rule.pop(), self._rs_arg.pop(), self._rs_line.pop())

    def peek_rule(self, n):
        return self._rs_rule[len(self._rs_rule) - 1 - n]

    def rule_depth(self):
        return len(self._rs_rule)

    def push_result_node(self, pn):
        self.result.append(pn)

    def pop_result(self):
        return self.result.pop()

    def peek_result(self, pos):
        return self.result[len(self.result) - 1 - pos]

    # -- constants -------------------------------------------------------

    def _new_const_node(self, src_line, value):
        """A RULE_const_object node holding *value*."""
        idx = self.tree.add_const(value)
        return self.tree.new_struct(G.RULE_const_object, src_line, [idx])

    def _new_const_optimised(self, src_line, value):
        """make_node_const_object_optimised: an integer small enough for the
        target's small int becomes a leaf; anything else a const object."""
        if type(value) is int and PT.fits_small_int(value):
            return PT.new_small_int(value)
        return self._new_const_node(src_line, value)

    def const_value(self, pn):
        """The Python value of a constant node, or None if it is not one.

        Returns a one-tuple so a legitimate None/False value is
        distinguishable from "not a constant".
        """
        if PT.is_small_int(pn):
            return (PT.small_int_value(pn),)
        if PT.is_struct(pn) and self.tree.struct_kind(pn) == G.RULE_const_object:
            return (self.tree.const(self.tree.child(pn, 0)),)
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
            return (PT.MpStr(self.pool.text(PT.leaf_arg(pn))),)
        if PT.is_struct(pn) and self.tree.struct_kind(pn) == G.RULE_atom_paren:
            # `()` survives as atom_paren with a NULL child, and counts as the
            # constant empty tuple (mp_parse_node_is_const).
            if PT.is_null(self.tree.child(pn, 0)):
                return ((),)
        return None

    def is_const(self, pn):
        return self.const_value(pn) is not None

    def number_value(self, pn):
        """mp_parse_node_get_number_maybe: an int or float, else None."""
        v = self.const_value(pn)
        if v is None:
            return None
        if type(v[0]) is int or type(v[0]) is float:
            return v
        return None

    def _is_const_bool(self, pn, value):
        """parse_node_is_const_bool: a constant whose truth equals *value*."""
        v = self.const_value(pn)
        if v is None:
            return False
        return bool(v[0]) == value

    def is_const_true(self, pn):
        return self._is_const_bool(pn, True)

    def is_const_false(self, pn):
        return self._is_const_bool(pn, False)

    # -- turning the current token into a node ---------------------------

    def push_result_token(self, rule_id):
        lex = self.lex
        kind = lex.tok_kind
        if kind == T.MP_TOKEN_NAME:
            qst = self.pool.intern(bytes(lex.vstr))
            # A standalone identifier may be a `const()` declared earlier.
            if rule_id == G.RULE_atom and qst in self.consts:
                pn = self._new_const_optimised(lex.tok_line, self.consts[qst])
            else:
                pn = PT.new_id(qst)
        elif kind == T.MP_TOKEN_INTEGER:
            value = parse_int_literal(bytes(lex.vstr), lex.tok_line)
            pn = self._new_const_optimised(lex.tok_line, value)
        elif kind == T.MP_TOKEN_FLOAT_OR_IMAG:
            value = parse_float_literal(bytes(lex.vstr), lex.tok_line)
            pn = self._new_const_node(lex.tok_line, value)
        elif kind == T.MP_TOKEN_STRING:
            raw = bytes(lex.vstr)
            if len(raw) <= ALLOC_PARSE_INTERN_STRING_LEN:
                qst = self.pool.intern(raw)
            else:
                # Long strings are only leaves if already interned; docstrings
                # are usually long and the compiler discards them.
                qst = self.pool.find(raw)
            if qst is not None:
                pn = PT.new_string(qst)
            else:
                pn = self._new_const_node(lex.tok_line, PT.MpStr(raw))
        elif kind == T.MP_TOKEN_BYTES:
            pn = self._new_const_node(lex.tok_line, bytes(lex.vstr))
        else:
            pn = PT.new_token(kind)
        self.push_result_node(pn)

    # -- folding ---------------------------------------------------------

    def fold_logical_constants(self, rule_id, num_args):
        """Short-circuit `or`/`and` chains and resolve `not`.

        Returns the new num_args, and whether the rule folded completely.
        """
        if rule_id == G.RULE_or_test or rule_id == G.RULE_and_test:
            is_or = rule_id == G.RULE_or_test
            copy_to = num_args
            i = copy_to
            while i > 0:
                i -= 1
                pn = self.peek_result(i)
                self.result[len(self.result) - copy_to] = pn
                if i == 0:
                    break
                if is_or:
                    if self.is_const_true(pn):
                        break
                    elif not self.is_const_false(pn):
                        copy_to -= 1
                else:
                    if self.is_const_false(pn):
                        break
                    elif not self.is_const_true(pn):
                        copy_to -= 1
            copy_to -= 1  # now the number of arguments to discard
            for _ in range(copy_to):
                self.pop_result()
            num_args -= copy_to
            return num_args, num_args == 1
        if rule_id == G.RULE_not_test_2:
            pn = self.peek_result(0)
            if self.is_const_false(pn):
                pn = PT.new_token(T.MP_TOKEN_KW_TRUE)
            elif self.is_const_true(pn):
                pn = PT.new_token(T.MP_TOKEN_KW_FALSE)
            else:
                return num_args, False
            self.pop_result()
            self.push_result_node(pn)
            return num_args, True
        return num_args, False

    def fold_constants(self, rule_id, num_args):
        """Fold a numeric expression, or a `const()` declaration.

        Folds whole expressions only, never partially: `1 + 2 + x` stays.
        """
        arg0 = None
        if rule_id in _RULE_IMPLIED_OP:
            op = _RULE_IMPLIED_OP[rule_id]
            v = self.number_value(self.peek_result(num_args - 1))
            if v is None:
                return False
            arg0 = v[0]
            i = num_args - 2
            while i >= 0:
                v = self.number_value(self.peek_result(i))
                if v is None:
                    return False
                arg0 = _apply(op, arg0, v[0])
                if arg0 is _FAILED:
                    return False
                i -= 1
        elif rule_id in (G.RULE_shift_expr, G.RULE_arith_expr, G.RULE_term):
            v = self.number_value(self.peek_result(num_args - 1))
            if v is None:
                return False
            arg0 = v[0]
            i = num_args - 2
            while i >= 1:
                v = self.number_value(self.peek_result(i - 1))
                if v is None:
                    return False
                tok = PT.leaf_arg(self.peek_result(i))
                op = _BINOPS.get(tok)
                if op is None:
                    return False
                arg0 = _apply(op, arg0, v[0])
                if arg0 is _FAILED:
                    return False
                i -= 2
        elif rule_id == G.RULE_factor_2:
            v = self.number_value(self.peek_result(0))
            if v is None:
                return False
            arg0 = v[0]
            tok = PT.leaf_arg(self.peek_result(1))
            if tok == T.MP_TOKEN_OP_TILDE:
                if type(arg0) is not int:
                    return False
                arg0 = ~arg0
            elif tok == T.MP_TOKEN_OP_MINUS:
                arg0 = -arg0
            elif tok == T.MP_TOKEN_OP_PLUS:
                arg0 = +arg0
            else:
                return False
        elif rule_id == G.RULE_expr_stmt:
            return self._fold_const_declaration()
        elif rule_id == G.RULE_atom_expr_normal:
            v = self._fold_module_const()
            if v is None:
                return False
            arg0 = v[0]
        else:
            return False

        for _ in range(num_args):
            self.pop_result()
        self.push_result_node(self._new_const_optimised(0, arg0))
        return True

    def _fold_const_declaration(self):
        """Handle ``id = const(value)``.

        Records the value for later substitution.  A leading underscore marks
        it private, and then the statement itself becomes ``pass``.
        """
        pn1 = self.peek_result(0)
        if PT.is_null(pn1):
            return False
        if PT.is_struct(pn1):
            k = self.tree.struct_kind(pn1)
            if k in (G.RULE_expr_stmt_augassign, G.RULE_expr_stmt_assign_list):
                return False
        pn0 = self.peek_result(1)
        if not PT.is_id(pn0):
            return False
        if PT.is_struct(pn1) and self.tree.struct_kind(pn1) == G.RULE_annassign:
            # `x: int = const(1)` -- take the right-hand side
            pn1 = self.tree.child(pn1, 1)
        if not (PT.is_struct(pn1)
                and self.tree.struct_kind(pn1) == G.RULE_atom_expr_normal):
            return False
        callee = self.tree.child(pn1, 0)
        trailer = self.tree.child(pn1, 1)
        if not PT.is_id(callee):
            return False
        if self.pool.text(PT.leaf_arg(callee)) != b"const":
            return False
        if not (PT.is_struct(trailer)
                and self.tree.struct_kind(trailer) == G.RULE_trailer_paren):
            return False

        qst = PT.leaf_arg(pn0)
        pn_value = self.tree.child(trailer, 0)
        v = self.const_value(pn_value)
        if v is None:
            raise ParseError("SyntaxError", "not a constant",
                             self.tree.source_line(pn1))
        self.consts[qst] = v[0]

        if self.pool.text(qst).startswith(b"_"):
            # Private: emit no store at all.
            self.pop_result()
            self.pop_result()
            self.emit_rule_node(0, G.RULE_pass_stmt, 0)
            return True

        # Replace const(value) with the value, but keep the assignment.
        self.pop_result()
        self.push_result_node(pn_value)
        return False

    def _fold_module_const(self):
        """MICROPY_COMP_MODULE_CONST: fold ``math.pi`` and friends.

        The C table is consulted by name alone, with no regard for whether the
        module was imported, so this does the same.  Only the modules mpy-cross
        actually links are in it: ``errno`` and ``uctypes`` are absent from the
        pinned build (probing the oracle leaves ``errno.ENOENT`` unfolded),
        leaving ``math``.
        """
        pn0 = self.peek_result(1)
        pn1 = self.peek_result(0)
        if not PT.is_id(pn0):
            return None
        if not (PT.is_struct(pn1)
                and self.tree.struct_kind(pn1) == G.RULE_trailer_period):
            return None
        attr_node = self.tree.child(pn1, 0)
        if not PT.is_id(attr_node):
            return None
        table = MODULE_CONSTANTS.get(self.pool.text(PT.leaf_arg(pn0)))
        if table is None:
            return None
        name = self.pool.text(PT.leaf_arg(attr_node))
        if name not in table:
            return None
        return (table[name],)

    def build_tuple_from_stack(self, src_line, num_args):
        """Fold a tuple of constants into one object."""
        i = num_args
        while i > 0:
            i -= 1
            if not self.is_const(self.peek_result(i)):
                return False
        items = [None] * num_args
        i = num_args
        while i > 0:
            pn = self.pop_result()
            i -= 1
            items[i] = self.const_value(pn)[0]
        self.push_result_node(self._new_const_node(src_line, tuple(items)))
        return True

    def build_tuple(self, src_line, rule_id, num_args):
        if rule_id == G.RULE_testlist_comp:
            if self.peek_rule(0) == G.RULE_atom_paren:
                # "(a,)" and friends
                return self.build_tuple_from_stack(src_line, num_args)
            return False
        if rule_id == G.RULE_testlist_comp_3c:
            if self.peek_rule(2) == G.RULE_atom_paren:
                # "(a, b)"
                if self.build_tuple_from_stack(src_line, num_args):
                    del self._rs_rule[len(self._rs_rule) - 2:]
                    del self._rs_arg[len(self._rs_arg) - 2:]
                    del self._rs_line[len(self._rs_line) - 2:]
                    return True
            return False
        if rule_id in (G.RULE_testlist_star_expr, G.RULE_testlist,
                       G.RULE_subscriptlist):
            # x = a, b   /   return a, b   /   for x in a, b   /   x[a, b]
            return self.build_tuple_from_stack(src_line, num_args)
        return False

    # -- emitting a rule's node ------------------------------------------

    def push_result_rule(self, src_line, rule_id, num_args):
        # Simplifications that reduce memory and keep the compiler simpler.
        if rule_id == G.RULE_atom_paren:
            pn = self.peek_result(0)
            if PT.is_null(pn):
                pass  # keep the parens for ()
            elif (PT.is_struct(pn)
                  and self.tree.struct_kind(pn) == G.RULE_testlist_comp):
                pass  # keep them for (a, b, ...)
            else:
                # parens around a single expression: just the expression
                return
        elif rule_id == G.RULE_testlist_comp:
            pn = self.peek_result(0)
            if PT.is_struct(pn):
                k = self.tree.struct_kind(pn)
                if k == G.RULE_testlist_comp_3b:
                    # one item with a trailing comma
                    self.pop_result()
                    num_args -= 1
                elif k == G.RULE_testlist_comp_3c:
                    # many items: relabel testlist_comp_3c as testlist_comp
                    self.pop_result()
                    slot = pn >> 2
                    n = self.tree.num_children(pn)
                    self.tree.arena[slot + 1] = rule_id | (n << 8)
                    return
        elif rule_id == G.RULE_testlist_comp_3c:
            # steal the first argument of the enclosing testlist_comp
            num_args += 1

        num_args, folded = self.fold_logical_constants(rule_id, num_args)
        if folded:
            return
        if self.fold_constants(rule_id, num_args):
            return
        if self.build_tuple(src_line, rule_id, num_args):
            return

        self.emit_rule_node(src_line, rule_id, num_args)

    def emit_rule_node(self, src_line, rule_id, num_args):
        """Build a rule's node unconditionally, with no folding attempted.

        Separate from push_result_rule so the `pass_stmt` replacements can
        reach it directly.  Routing those through the full path would make the
        call graph cyclic (folding calls back into push_result_rule), and a
        zero-argument `pass_stmt` can neither fold nor build a tuple, so the
        behaviour is identical.
        """
        children = [PT.NODE_NULL] * num_args
        i = num_args
        while i > 0:
            i -= 1
            children[i] = self.pop_result()
        node = self.tree.new_struct(rule_id, src_line, children)
        if rule_id == G.RULE_testlist_comp_3c:
            # replace the stolen first argument
            self.push_result_node(node)
        self.push_result_node(node)

    # -- the main loop ---------------------------------------------------

    def parse(self):
        lex = self.lex
        if self.input_kind == PARSE_SINGLE_INPUT:
            top = G.RULE_single_input
        elif self.input_kind == PARSE_EVAL_INPUT:
            top = G.RULE_eval_input
        else:
            top = G.RULE_file_input
        self.push_rule(lex.tok_line, top, 0)

        backtrack = False
        while self.rule_depth() > 0:
            rule_id, i, rule_src_line = self.pop_rule()
            rule_act = G.RULE_ACT[rule_id]
            n = rule_act & G.RULE_ACT_ARG_MASK
            kind = rule_act & G.RULE_ACT_KIND_MASK
            base = G.RULE_ARG_OFFSET[rule_id]
            args = G.RULE_ARG

            if kind == G.RULE_ACT_OR:
                if i > 0 and not backtrack:
                    continue
                backtrack = False
                matched = False
                while i < n:
                    arg = args[base + i]
                    if (arg & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_TOK:
                        if lex.tok_kind == (arg & G.RULE_ARG_ARG_MASK):
                            self.push_result_token(rule_id)
                            lex.to_next()
                            matched = True
                            break
                    else:
                        if i + 1 < n:
                            self.push_rule(rule_src_line, rule_id, i + 1)
                        self.push_rule_from_arg(arg)
                        matched = True
                        break
                    i += 1
                if matched:
                    continue
                backtrack = True
                continue

            if kind == G.RULE_ACT_AND:
                if backtrack:
                    prev = args[base + i - 1]
                    if (prev & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_OPT_RULE:
                        # an optional rule failed: carry on with the next arg
                        self.push_result_node(PT.NODE_NULL)
                        backtrack = False
                    else:
                        if i > 1:
                            self.syntax_error()
                        continue  # propagate the backtrack
                advanced = False
                while i < n:
                    arg = args[base + i]
                    if (arg & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_TOK:
                        tok_kind = arg & G.RULE_ARG_ARG_MASK
                        if lex.tok_kind == tok_kind:
                            if tok_kind == T.MP_TOKEN_NAME:
                                self.push_result_token(rule_id)
                            lex.to_next()
                        else:
                            if i > 0:
                                self.syntax_error()
                            backtrack = True
                            advanced = True
                            break
                    else:
                        self.push_rule(rule_src_line, rule_id, i + 1)
                        self.push_rule_from_arg(arg)
                        advanced = True
                        break
                    i += 1
                if advanced:
                    continue

                # The rule matched; build its node.
                if (self.input_kind != PARSE_SINGLE_INPUT
                        and rule_id == G.RULE_expr_stmt
                        and PT.is_null(self.peek_result(0))):
                    # With docstrings disabled, a lonely constant statement is
                    # discarded and replaced by "pass".
                    p = self.peek_result(1)
                    lonely = False
                    if PT.is_leaf(p) and not PT.is_id(p):
                        lonely = True
                    elif (PT.is_struct(p)
                          and self.tree.struct_kind(p) == G.RULE_const_object):
                        lonely = True
                    if lonely:
                        self.pop_result()
                        self.pop_result()
                        self.emit_rule_node(rule_src_line, G.RULE_pass_stmt, 0)
                        continue

                # Count the arguments actually on the stack.
                i = 0
                num_not_nil = 0
                x = n
                while x > 0:
                    x -= 1
                    arg = args[base + x]
                    if (arg & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_TOK:
                        if (arg & G.RULE_ARG_ARG_MASK) == T.MP_TOKEN_NAME:
                            # only name tokens were pushed
                            i += 1
                            num_not_nil += 1
                    else:
                        if not PT.is_null(self.peek_result(i)):
                            num_not_nil += 1
                        i += 1

                if num_not_nil == 1 and (rule_act & G.RULE_ACT_ALLOW_IDENT):
                    # Single argument: emit no node, pass the argument up.
                    pn = PT.NODE_NULL
                    for _ in range(i):
                        pn2 = self.pop_result()
                        if not PT.is_null(pn2):
                            pn = pn2
                    self.push_result_node(pn)
                else:
                    if rule_act & G.RULE_ACT_ADD_BLANK:
                        # an extra slot the compiler uses for its own data
                        self.push_result_node(PT.NODE_NULL)
                        i += 1
                    self.push_result_rule(rule_src_line, rule_id, i)
                continue

            # RULE_ACT_LIST.  n=2: item item*   n=1: item (sep item)*
            # n=3: item (sep item)* [sep]
            #
            # C reaches the backtrack handling both on entry and via
            # `goto list_backtrack` from inside the matching loop, so the two
            # are one loop here with explicit flags in place of the gotos.
            propagate = False
            had_trailing_sep = False
            while True:
                if backtrack:
                    had_trailing_sep = False
                    if i == 1:
                        # failed on the first item: propagate the backtrack
                        propagate = True
                        break
                    if n == 2:
                        backtrack = False
                    elif (i & 1) == 1:
                        # failed on an item in a later round, so tokens have
                        # been eaten and backtracking is no longer possible
                        if n == 3:
                            had_trailing_sep = True
                            backtrack = False
                        else:
                            self.syntax_error()
                    else:
                        # failed on a separator: the list simply ends
                        backtrack = False
                    break  # the list is complete

                arg = args[base + (i & 1 & n)]
                if (arg & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_TOK:
                    if lex.tok_kind == (arg & G.RULE_ARG_ARG_MASK):
                        if not (i & 1 & n):
                            # separators that are tokens are not pushed
                            self.push_result_token(rule_id)
                        lex.to_next()
                        i += 1
                        continue
                    # could not get an element; re-enter the backtrack arm
                    i += 1
                    backtrack = True
                    continue
                self.push_rule(rule_src_line, rule_id, i + 1)
                self.push_rule_from_arg(arg)
                propagate = True
                break

            if propagate:
                continue

            # Work out how many elements the list has.
            i -= 1
            if ((n & 1)
                    and (args[base + 1] & G.RULE_ARG_KIND_MASK) == G.RULE_ARG_TOK):
                # token separators are not counted
                i = (i + 1) // 2
            if i == 1 and not had_trailing_sep:
                pass  # a single item is left unwrapped
            else:
                self.push_result_rule(rule_src_line, rule_id, i)

        if lex.tok_kind != T.MP_TOKEN_END or len(self.result) == 0:
            self.syntax_error()
        self.tree.root = self.result[0]
        return self.tree

    def syntax_error(self):
        lex = self.lex
        if lex.tok_kind == T.MP_TOKEN_INDENT:
            raise ParseError("IndentationError", "unexpected indent",
                             lex.tok_line)
        if lex.tok_kind == T.MP_TOKEN_DEDENT_MISMATCH:
            raise ParseError("IndentationError",
                             "unindent doesn't match any outer indent level",
                             lex.tok_line)
        if lex.tok_kind == T.MP_TOKEN_MALFORMED_FSTRING:
            raise ParseError("SyntaxError", "malformed f-string", lex.tok_line)
        raise ParseError("SyntaxError", "invalid syntax", lex.tok_line)


class _Failed:
    pass


_FAILED = _Failed()


def _apply(op, a, b):
    """Apply a folding operation, or report failure the way
    ``binary_op_maybe`` does: anything that raises, or yields a complex,
    leaves the expression unfolded."""
    try:
        r = op(a, b)
    except Exception:  # noqa: BLE001
        return _FAILED
    if type(r) is complex:
        return _FAILED
    return r


def parse_int_literal(raw, line):
    text = raw.decode("ascii", "replace")
    try:
        if len(text) > 1 and text[0] == "0" and len(text) > 2 and text[1] in "xXoObB":
            return int(text, 0)
        return int(text, 10)
    except ValueError:
        raise ParseError("SyntaxError", "invalid syntax", line)


def parse_float_literal(raw, line):
    text = raw.decode("ascii", "replace")
    try:
        if text.endswith("j") or text.endswith("J"):
            # Imaginary literals are complex; the parser stores the object and
            # never folds with it.
            return complex(0.0, float(text[:-1] or "1"))
        return float(text)
    except ValueError:
        raise ParseError("SyntaxError", "invalid syntax", line)


def parse(source, filename="<stdin>", input_kind=PARSE_FILE_INPUT,
          optimise_value=0):
    """Parse *source*, returning a ``ParseTree``."""
    lex = Lexer(filename, source, optimise_value)
    return Parser(lex, input_kind).parse()
