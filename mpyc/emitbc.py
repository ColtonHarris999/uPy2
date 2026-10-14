"""The bytecode emitter: a port of ``py/emitbc.c``.

The emitter is a state machine, not a function.  It is driven four times over
the same scope -- ``MP_PASS_SCOPE``, ``STACK_SIZE``, ``CODE_SIZE``, ``EMIT`` --
and each pass learns something the next one needs:

* **SCOPE** decides which names are local, cell or free, and counts labels.
  Nothing is written; the emitter only interns the function's name and
  argument names so their table slots are claimed in the right order.
* **STACK_SIZE** tracks ``stack_size`` through every push and pop to find the
  scope's maximum, which the prelude records.
* **CODE_SIZE** counts bytes to size the buffers and to place every label.
* **EMIT** writes the bytes.  Jumps shrink when their target turns out to be
  near, so this pass can report that the code got smaller and ask to be run
  again; it is repeated until the size settles.

Two mechanisms make that work and are easy to get subtly wrong:

* ``suppress`` drops everything emitted after an unconditional jump, raise or
  return until the next label -- MicroPython's whole dead-code elimination.
  A suppressed write advances no offset, so the dead bytes never exist.
* A jump's offset may be one or two bytes.  The encoding may only ever
  *shrink* from one pass to the next, or a label placed earlier in the pass
  would already have been written with a stale distance.

``EmitCommon`` holds what all the scopes in one module share: the qstr table,
the constant-object table and the list of child functions.  Those three are
exactly the tables an ``.mpy`` file carries at module level.
"""

from . import bytecode as bc
from . import opcodes as O
from . import runtime0 as R
from . import scope as S
from . import tokens as T
from .parsetree import MpStr, const_key
from .qstrtable import QstrTable

# -- passes (py/emit.h) ---------------------------------------------------

MP_PASS_SCOPE = 1       # work out ids and their kind, and the label count
MP_PASS_STACK_SIZE = 2  # work out the maximum stack size
MP_PASS_CODE_SIZE = 3   # work out code size and label offsets
MP_PASS_EMIT = 4        # emit code (may run more than once)

PASS_NAMES = (None, "SCOPE", "STACK_SIZE", "CODE_SIZE", "EMIT")

# -- emitter operation kinds (py/emit.h) ---------------------------------

MP_EMIT_STAR_FLAG_SINGLE = 0x01
MP_EMIT_STAR_FLAG_DOUBLE = 0x02

# The compiler ORs this into a break label to say "also pop the iterator".
MP_EMIT_BREAK_FROM_FOR = 0x8000

MP_EMIT_IDOP_LOCAL_FAST = 0
MP_EMIT_IDOP_LOCAL_DEREF = 1

MP_EMIT_IDOP_GLOBAL_NAME = 0
MP_EMIT_IDOP_GLOBAL_GLOBAL = 1

MP_EMIT_IMPORT_NAME = 0
MP_EMIT_IMPORT_FROM = 1
MP_EMIT_IMPORT_STAR = 2

MP_EMIT_SUBSCR_LOAD = 0
MP_EMIT_SUBSCR_STORE = 1
MP_EMIT_SUBSCR_DELETE = 2

MP_EMIT_ATTR_LOAD = 0
MP_EMIT_ATTR_STORE = 1
MP_EMIT_ATTR_DELETE = 2

MP_EMIT_SETUP_BLOCK_WITH = 0
MP_EMIT_SETUP_BLOCK_EXCEPT = 1
MP_EMIT_SETUP_BLOCK_FINALLY = 2

MP_EMIT_BUILD_TUPLE = 0
MP_EMIT_BUILD_LIST = 1
MP_EMIT_BUILD_MAP = 2
MP_EMIT_BUILD_SET = 3
MP_EMIT_BUILD_SLICE = 4

MP_EMIT_YIELD_VALUE = 0
MP_EMIT_YIELD_FROM = 1

# Emit options (py/compile.h).  Only bytecode is emitted here, so the native
# and inline-assembler options exist to be rejected.
MP_EMIT_OPT_NONE = 0
MP_EMIT_OPT_BYTECODE = 1
MP_EMIT_OPT_NATIVE_PYTHON = 2
MP_EMIT_OPT_VIPER = 3
MP_EMIT_OPT_ASM = 4

# A label offset that is not yet known.  The CODE_SIZE pass reads a forward
# label before assigning it, and C reads whatever m_new left in the array; the
# invariant it relies on is only that the pass over-estimates, since the EMIT
# pass re-measures and may shrink.  A deliberately huge value forces the wide
# jump encoding, which is that over-estimate made explicit.
LABEL_UNKNOWN = 0x7FFFFFFF


class EmitError(Exception):
    pass


class EmitCommon:
    """What every scope in one module shares.

    These are the module-level tables an ``.mpy`` carries: the qstr table that
    bytecode operands index, the constant objects that ``LOAD_CONST_OBJ``
    indexes, and the child functions that ``MAKE_FUNCTION`` indexes.
    """

    def __init__(self, pool, source_file):
        self.pool = pool
        self.qstr_table = QstrTable(pool)
        # The source file is entry 0, claimed before anything else is emitted.
        self.qstr_table.index(source_file)
        self.const_objs = []
        self._const_index = {}
        # Child raw codes, in allocation order.  Re-walked every pass: the
        # list is sized from the previous pass's count and refilled, so a
        # child's index is stable as long as the walk order is.
        self.children = []
        self.ct_cur_child = 0
        self.pass_ = MP_PASS_SCOPE

    def start_pass(self, pass_):
        self.pass_ = pass_
        if pass_ == MP_PASS_CODE_SIZE:
            self.children = [None] * self.ct_cur_child
        self.ct_cur_child = 0

    def use_qstr(self, qst):
        """The module qstr-table index for qstr number *qst*."""
        return self.qstr_table.index(qst)

    def use_const_obj(self, value):
        """The constant-table index for *value*, appending it on first use.

        ``strictly_equal`` in ``py/emitcommon.c`` compares type as well as
        value, so ``1``, ``1.0`` and ``True`` are three constants, and
        ``0.0``/``-0.0`` are two.  ``const_key`` draws exactly those
        distinctions, keying floats on their raw bits.
        """
        key = const_key(value)
        i = self._const_index.get(key)
        if i is not None:
            return i
        i = len(self.const_objs)
        self.const_objs.append(value)
        self._const_index[key] = i
        return i

    def alloc_const_child(self, scope):
        """Claim this pass's index for a nested function's code."""
        i = self.ct_cur_child
        if i < len(self.children):
            self.children[i] = scope
        self.ct_cur_child = i + 1
        return i


class Emit:
    """The bytecode emitter for one scope at a time."""

    def __init__(self, emit_common):
        self.emit_common = emit_common
        self.pass_ = MP_PASS_SCOPE
        # Drop code that follows an unconditional jump, raise or return.
        self.suppress = False
        self.stack_size = 0
        self.scope = None
        self.last_source_line_offset = 0
        self.last_source_line = 1
        self.label_offsets = []
        self.code_info_offset = 0
        self.code_info_size = 0
        self.bytecode_offset = 0
        self.bytecode_size = 0
        self.code_info = None
        self.bytecode = None
        self.overflow = False
        self.n_info = 0
        self.n_cell = 0
        # mp_optimise_value: -O3 drops line numbers entirely.
        self.optimise_value = 0
        self.include_source_lines = True

    def set_max_num_labels(self, n):
        self.label_offsets = [LABEL_UNKNOWN] * n

    # -- raw writes ------------------------------------------------------

    def _ci(self, data):
        """Write *data* to the code-info section."""
        if self.pass_ >= MP_PASS_EMIT:
            self.code_info.extend(data)
        self.code_info_offset += len(data)

    def _ci_byte(self, b):
        if self.pass_ >= MP_PASS_EMIT:
            self.code_info.append(b)
        self.code_info_offset += 1

    def _ci_qstr(self, qst):
        buf = bytearray()
        bc.encode_uint(self.emit_common.use_qstr(qst), buf)
        self._ci(buf)

    def _bc(self, data):
        """Write *data* to the bytecode, unless it is dead code."""
        if self.suppress:
            return
        if self.pass_ >= MP_PASS_EMIT:
            self.bytecode.extend(data)
        self.bytecode_offset += len(data)

    def _bc_raw_byte(self, b):
        """A byte that is part of the previous instruction, not a new one."""
        if self.suppress:
            return
        if self.pass_ >= MP_PASS_EMIT:
            self.bytecode.append(b)
        self.bytecode_offset += 1

    def _op(self, stack_adj, opcode):
        self.adjust_stack_size(stack_adj)
        self._bc_raw_byte(opcode)

    def _op_int(self, stack_adj, opcode, num):
        """An opcode with a signed varint argument."""
        self._op(stack_adj, opcode)
        buf = bytearray()
        bc.encode_signed_int(num, buf)
        self._bc(buf)

    def _op_uint(self, stack_adj, opcode, val):
        self._op(stack_adj, opcode)
        buf = bytearray()
        bc.encode_uint(val, buf)
        self._bc(buf)

    def _op_qstr(self, stack_adj, opcode, qst):
        self._op_uint(stack_adj, opcode, self.emit_common.use_qstr(qst))

    def _op_obj(self, stack_adj, opcode, value):
        self._op_uint(stack_adj, opcode,
                      self.emit_common.use_const_obj(value))

    def _op_child(self, stack_adj, opcode, scope):
        self._op_uint(stack_adj, opcode,
                      self.emit_common.alloc_const_child(scope))

    def _op_label(self, stack_adj, opcode, label):
        """A jump to *label*, in the narrowest encoding that will hold.

        The offset is measured from the instruction *after* this one, so the
        instruction's own length is part of the arithmetic -- and the length
        depends on the offset, which is why the two-byte form is assumed until
        the code size is known.
        """
        self.adjust_stack_size(stack_adj)
        if self.suppress:
            return
        signed = O.has_signed_offset(opcode)
        # 0 means a one-byte offset, 1 means two bytes.
        wide = 1
        offset = 0
        if self.pass_ >= MP_PASS_CODE_SIZE:
            # -2 for this instruction, which is at least two bytes long.
            offset = self.label_offsets[label] - self.bytecode_offset - 2
            if ((signed and -64 <= offset <= 63)
                    or (not signed and 0 <= offset <= 127)):
                wide = 0
            offset -= wide
            if not signed and offset < 0:
                raise EmitError("backward jump with an unsigned offset")
        if self.pass_ == MP_PASS_EMIT and wide:
            v = offset + 0x4000 if signed else offset
            if not 0 <= v <= 0x7FFF:
                # Reported at the end of the pass, as in C: the byte count
                # must still come out right for the caller to act on.
                self.overflow = True
        buf = bytearray()
        buf.append(opcode)
        try:
            bc.encode_offset(opcode, offset, buf, 1 if wide == 0 else 2)
        except ValueError:
            # Out of range only matters on the final pass, where overflow is
            # already recorded; earlier passes just need the right length.
            del buf[1:]
            buf.append(0)
            if wide:
                buf.append(0)
        self._bc(buf)

    # -- passes ----------------------------------------------------------

    def start_pass(self, pass_, scope):
        self.pass_ = pass_
        self.stack_size = 0
        self.suppress = False
        self.scope = scope
        self.last_source_line_offset = 0
        self.last_source_line = 1
        self.bytecode_offset = 0
        self.code_info_offset = 0
        self.overflow = False
        if pass_ >= MP_PASS_EMIT:
            self.code_info = bytearray()
            self.bytecode = bytearray()

        # The prelude's signature: state size, exception stack, scope flags
        # and the argument counts.
        n_state = scope.num_locals + scope.stack_size
        if n_state == 0:
            # At least one slot is needed: an exception propagating out of
            # this function is returned in the highest state slot.
            n_state = 1
        buf = bytearray()
        bc.encode_prelude_sig(n_state, scope.exc_stack_size,
                              scope.scope_flags & S.MP_SCOPE_FLAG_ALL_SIG,
                              scope.num_pos_args, scope.num_kwonly_args,
                              scope.num_def_pos_args, buf)
        self._ci(buf)

        # The sizes of the two sections that follow are only known once a
        # pass has measured them.
        if pass_ >= MP_PASS_CODE_SIZE:
            buf = bytearray()
            bc.encode_prelude_size(self.n_info, self.n_cell, buf)
            self._ci(buf)

        # From here to the end of the line table is the "source info"
        # section, whose length is n_info.
        self.n_info = self.code_info_offset

        self._ci_qstr(scope.simple_name)

        # One name per argument, to resolve positional args passed by
        # keyword.  Argument i is not identifier i: closed-over variables are
        # interleaved, so the entry with ID_FLAG_IS_PARAM and local_num == i
        # is the one wanted.  An argument slot with no parameter -- a
        # closed-over variable occupying it -- is named "*", which no real
        # argument can be called.
        n_args = scope.num_pos_args + scope.num_kwonly_args
        for i in range(n_args):
            qst = self.emit_common.pool.intern(b"*")
            for j in range(scope.num_ids()):
                if scope.id_is_param(j) and scope.id_local_num(j) == i:
                    qst = scope.id_qst(j)
                    break
            self._ci_qstr(qst)

    def end_pass(self):
        """Finish the pass.  False asks the compiler to run EMIT again."""
        if self.pass_ == MP_PASS_SCOPE:
            return True

        if self.stack_size != 0:
            raise EmitError("stack ends at %d, not 0, in %s"
                            % (self.stack_size, PASS_NAMES[self.pass_]))

        self.n_info = self.code_info_offset - self.n_info

        # The closure section: one byte per cell, giving the local to convert.
        self.n_cell = 0
        scope = self.scope
        for i in range(scope.num_ids()):
            if scope.id_kind(i) == S.ID_INFO_KIND_CELL:
                local_num = scope.id_local_num(i)
                if local_num > 255:
                    raise EmitError("more than 256 locals to close over")
                self._ci_byte(local_num)
                self.n_cell += 1

        if self.pass_ == MP_PASS_CODE_SIZE:
            self.code_info_size = self.code_info_offset
            self.bytecode_size = self.bytecode_offset
            return True

        if self.pass_ == MP_PASS_EMIT:
            if (self.code_info_offset > self.code_info_size
                    or self.bytecode_offset > self.bytecode_size):
                raise EmitError("code grew during the emit pass")
            if (self.code_info_offset != self.code_info_size
                    or self.bytecode_offset != self.bytecode_size):
                # Something shrank, so the label offsets moved; go again.
                self.code_info_size = self.code_info_offset
                self.bytecode_size = self.bytecode_offset
                return False
            if self.overflow:
                raise EmitError("bytecode overflow")
            self.scope.raw_code = bytes(self.code_info) + bytes(self.bytecode)
            self.scope.raw_code_children = list(
                self.emit_common.children[:self.emit_common.ct_cur_child])
        return True

    def adjust_stack_size(self, delta):
        if self.pass_ == MP_PASS_SCOPE:
            return
        if self.stack_size + delta < 0:
            raise EmitError("stack would go negative (%d%+d)"
                            % (self.stack_size, delta))
        self.stack_size += delta
        if self.stack_size > self.scope.stack_size:
            self.scope.stack_size = self.stack_size

    def set_source_line(self, source_line):
        if self.optimise_value >= 3 or not self.include_source_lines:
            return
        if source_line > self.last_source_line:
            buf = bytearray()
            bc.encode_line_info(self.bytecode_offset
                                - self.last_source_line_offset,
                                source_line - self.last_source_line, buf)
            self._ci(buf)
            self.last_source_line_offset = self.bytecode_offset
            self.last_source_line = source_line

    def label_assign(self, label):
        # A label ends any dead-code region.
        self.suppress = False
        if self.pass_ == MP_PASS_SCOPE:
            return
        if self.pass_ != MP_PASS_STACK_SIZE:
            if self.bytecode_offset > self.label_offsets[label]:
                raise EmitError("label %d moved forward, from %d to %d"
                                % (label, self.label_offsets[label],
                                   self.bytecode_offset))
        self.label_offsets[label] = self.bytecode_offset

    # -- loads and stores ------------------------------------------------

    def import_(self, qst, kind):
        stack_adj = 1 if kind == MP_EMIT_IMPORT_FROM else -1
        if kind == MP_EMIT_IMPORT_STAR:
            self._op(stack_adj, O.MP_BC_IMPORT_STAR)
        else:
            self._op_qstr(stack_adj, O.MP_BC_IMPORT_NAME + kind, qst)

    def load_const_tok(self, tok):
        if tok == T.MP_TOKEN_ELLIPSIS:
            self._op_obj(1, O.MP_BC_LOAD_CONST_OBJ, Ellipsis)
        else:
            self._op(1, O.MP_BC_LOAD_CONST_FALSE
                     + (tok - T.MP_TOKEN_KW_FALSE))

    def load_const_small_int(self, arg):
        lo = -O.MP_BC_LOAD_CONST_SMALL_INT_MULTI_EXCESS
        hi = (O.MP_BC_LOAD_CONST_SMALL_INT_MULTI_NUM
              - O.MP_BC_LOAD_CONST_SMALL_INT_MULTI_EXCESS)
        if lo <= arg < hi:
            self._op(1, O.MP_BC_LOAD_CONST_SMALL_INT_MULTI
                     + O.MP_BC_LOAD_CONST_SMALL_INT_MULTI_EXCESS + arg)
        else:
            self._op_int(1, O.MP_BC_LOAD_CONST_SMALL_INT, arg)

    def load_const_str(self, qst):
        self._op_qstr(1, O.MP_BC_LOAD_CONST_STRING, qst)

    def load_const_obj(self, value):
        self._op_obj(1, O.MP_BC_LOAD_CONST_OBJ, value)

    def load_null(self):
        self._op(1, O.MP_BC_LOAD_NULL)

    def load_local(self, qst, local_num, kind):
        if kind == MP_EMIT_IDOP_LOCAL_FAST and local_num <= 15:
            self._op(1, O.MP_BC_LOAD_FAST_MULTI + local_num)
        else:
            self._op_uint(1, O.MP_BC_LOAD_FAST_N + kind, local_num)

    def load_global(self, qst, kind):
        self._op_qstr(1, O.MP_BC_LOAD_NAME + kind, qst)

    def load_method(self, qst, is_super):
        stack_adj = 1 - 2 * (1 if is_super else 0)
        self._op_qstr(stack_adj,
                      O.MP_BC_LOAD_SUPER_METHOD if is_super
                      else O.MP_BC_LOAD_METHOD, qst)

    def load_build_class(self):
        self._op(1, O.MP_BC_LOAD_BUILD_CLASS)

    def subscr(self, kind):
        if kind == MP_EMIT_SUBSCR_LOAD:
            self._op(-1, O.MP_BC_LOAD_SUBSCR)
        else:
            if kind == MP_EMIT_SUBSCR_DELETE:
                self.load_null()
                self.rot_three()
            self._op(-3, O.MP_BC_STORE_SUBSCR)

    def attr(self, qst, kind):
        if kind == MP_EMIT_ATTR_LOAD:
            self._op_qstr(0, O.MP_BC_LOAD_ATTR, qst)
        else:
            if kind == MP_EMIT_ATTR_DELETE:
                self.load_null()
                self.rot_two()
            self._op_qstr(-2, O.MP_BC_STORE_ATTR, qst)

    def store_local(self, qst, local_num, kind):
        if kind == MP_EMIT_IDOP_LOCAL_FAST and local_num <= 15:
            self._op(-1, O.MP_BC_STORE_FAST_MULTI + local_num)
        else:
            self._op_uint(-1, O.MP_BC_STORE_FAST_N + kind, local_num)

    def store_global(self, qst, kind):
        self._op_qstr(-1, O.MP_BC_STORE_NAME + kind, qst)

    def delete_local(self, qst, local_num, kind):
        self._op_uint(0, O.MP_BC_DELETE_FAST + kind, local_num)

    def delete_global(self, qst, kind):
        self._op_qstr(0, O.MP_BC_DELETE_NAME + kind, qst)

    # -- stack shuffling -------------------------------------------------

    def dup_top(self):
        self._op(1, O.MP_BC_DUP_TOP)

    def dup_top_two(self):
        self._op(2, O.MP_BC_DUP_TOP_TWO)

    def pop_top(self):
        self._op(-1, O.MP_BC_POP_TOP)

    def rot_two(self):
        self._op(0, O.MP_BC_ROT_TWO)

    def rot_three(self):
        self._op(0, O.MP_BC_ROT_THREE)

    # -- control flow ----------------------------------------------------

    def jump(self, label):
        self._op_label(0, O.MP_BC_JUMP, label)
        self.suppress = True

    def pop_jump_if(self, cond, label):
        self._op_label(-1, O.MP_BC_POP_JUMP_IF_TRUE if cond
                       else O.MP_BC_POP_JUMP_IF_FALSE, label)

    def jump_if_or_pop(self, cond, label):
        self._op_label(-1, O.MP_BC_JUMP_IF_TRUE_OR_POP if cond
                       else O.MP_BC_JUMP_IF_FALSE_OR_POP, label)

    def unwind_jump(self, label, except_depth):
        if except_depth == 0:
            if label & MP_EMIT_BREAK_FROM_FOR:
                # Breaking out of a for loop: drop the iterator and the slots
                # of its buffer.
                self._bc_raw_byte(O.MP_BC_POP_TOP)
                for _ in range(R.MP_OBJ_ITER_BUF_NSLOTS - 1):
                    self._bc_raw_byte(O.MP_BC_POP_TOP)
            self._op_label(0, O.MP_BC_JUMP,
                           label & ~MP_EMIT_BREAK_FROM_FOR)
        else:
            self._op_label(0, O.MP_BC_UNWIND_JUMP,
                           label & ~MP_EMIT_BREAK_FROM_FOR)
            self._bc_raw_byte((0x80 if label & MP_EMIT_BREAK_FROM_FOR else 0)
                              | except_depth)
        self.suppress = True

    def setup_block(self, label, kind):
        # SETUP_WITH pops the context manager and pushes three entries.
        stack_adj = 2 if kind == MP_EMIT_SETUP_BLOCK_WITH else 0
        self._op_label(stack_adj, O.MP_BC_SETUP_WITH + kind, label)

    def with_cleanup(self, label):
        self.load_const_tok(T.MP_TOKEN_KW_NONE)
        self.label_assign(label)
        # +2 so there is room to call __exit__ ...
        self._op(2, O.MP_BC_WITH_CLEANUP)
        # ... then undo that and the +2 from setup_block.
        self.adjust_stack_size(-4)

    def async_with_setup_finally(self, label_aexit_no_exc, label_finally_block,
                                 label_ret_unwind_jump):
        # The body finished without raising, so fall through to __aexit__.
        self.load_const_tok(T.MP_TOKEN_KW_NONE)
        self.rot_two()
        self.jump(label_aexit_no_exc)
        # The finally block, reached by an exception, a return or an unwind
        # jump.  Which one is told apart by whether the top of the stack is
        # an exception.
        self.label_assign(label_finally_block)
        self.dup_top()
        self.load_global(self.emit_common.pool.intern(b"BaseException"),
                         MP_EMIT_IDOP_GLOBAL_GLOBAL)
        self.binary_op(R.MP_BINARY_OP_EXCEPTION_MATCH)
        self.pop_jump_if(False, label_ret_unwind_jump)

    def end_finally(self):
        self._op(-1, O.MP_BC_END_FINALLY)

    def get_iter(self, use_stack):
        stack_adj = R.MP_OBJ_ITER_BUF_NSLOTS - 1 if use_stack else 0
        self._op(stack_adj, O.MP_BC_GET_ITER_STACK if use_stack
                 else O.MP_BC_GET_ITER)

    def for_iter(self, label):
        self._op_label(1, O.MP_BC_FOR_ITER, label)

    def for_iter_end(self):
        self.adjust_stack_size(-R.MP_OBJ_ITER_BUF_NSLOTS)

    def pop_except_jump(self, label, within_exc_handler):
        self._op_label(0, O.MP_BC_POP_EXCEPT_JUMP, label)
        self.suppress = True

    def start_except_handler(self):
        # The exception instance, plus 3 for a possible UNWIND_JUMP state.
        self.adjust_stack_size(4)

    def end_except_handler(self):
        self.adjust_stack_size(-3)

    # -- operators and builders ------------------------------------------

    def unary_op(self, op):
        self._op(0, O.MP_BC_UNARY_OP_MULTI + op)

    def binary_op(self, op):
        # "not in" and "is not" have no opcodes; they are the plain operator
        # followed by a logical not.
        invert = False
        if op == R.MP_BINARY_OP_NOT_IN:
            invert = True
            op = R.MP_BINARY_OP_IN
        elif op == R.MP_BINARY_OP_IS_NOT:
            invert = True
            op = R.MP_BINARY_OP_IS
        self._op(-1, O.MP_BC_BINARY_OP_MULTI + op)
        if invert:
            self._op(0, O.MP_BC_UNARY_OP_MULTI + R.MP_UNARY_OP_NOT)

    def build(self, n_args, kind):
        stack_adj = 1 if kind == MP_EMIT_BUILD_MAP else 1 - n_args
        self._op_uint(stack_adj, O.MP_BC_BUILD_TUPLE + kind, n_args)

    def store_map(self):
        self._op(-2, O.MP_BC_STORE_MAP)

    def store_comp(self, kind, collection_stack_index):
        if kind == S.SCOPE_LIST_COMP:
            n, t = 0, 0
        elif kind == S.SCOPE_DICT_COMP:
            n, t = 1, 1
        else:
            n, t = 0, 2
        # The low 2 bits of the argument say which kind of collection.
        self._op_uint(-1 - n, O.MP_BC_STORE_COMP,
                      ((collection_stack_index + n) << 2) | t)

    def unpack_sequence(self, n_args):
        self._op_uint(-1 + n_args, O.MP_BC_UNPACK_SEQUENCE, n_args)

    def unpack_ex(self, n_left, n_right):
        self._op_uint(-1 + n_left + n_right + 1, O.MP_BC_UNPACK_EX,
                      n_left | (n_right << 8))

    # -- functions and calls ---------------------------------------------

    def make_function(self, scope, n_pos_defaults, n_kw_defaults):
        if n_pos_defaults == 0 and n_kw_defaults == 0:
            self._op_child(1, O.MP_BC_MAKE_FUNCTION, scope)
        else:
            self._op_child(-1, O.MP_BC_MAKE_FUNCTION_DEFARGS, scope)

    def make_closure(self, scope, n_closed_over, n_pos_defaults,
                     n_kw_defaults):
        if n_closed_over > 255:
            raise EmitError("more than 255 closed-over variables")
        if n_pos_defaults == 0 and n_kw_defaults == 0:
            self._op_child(1 - n_closed_over, O.MP_BC_MAKE_CLOSURE, scope)
        else:
            self._op_child(-1 - n_closed_over, O.MP_BC_MAKE_CLOSURE_DEFARGS,
                           scope)
        self._bc_raw_byte(n_closed_over)

    def _call_helper(self, stack_adj, base, n_positional, n_keyword,
                     star_flags):
        if star_flags:
            # Each positional argument is one object, each keyword argument
            # two, plus one for the star-args bitmap.
            stack_adj -= n_positional + 2 * n_keyword + 1
            self._op_uint(stack_adj, base + 1,
                          (n_keyword << 8) | n_positional)
        else:
            stack_adj -= n_positional + 2 * n_keyword
            self._op_uint(stack_adj, base, (n_keyword << 8) | n_positional)

    def call_function(self, n_positional, n_keyword, star_flags):
        self._call_helper(0, O.MP_BC_CALL_FUNCTION, n_positional, n_keyword,
                          star_flags)

    def call_method(self, n_positional, n_keyword, star_flags):
        self._call_helper(-1, O.MP_BC_CALL_METHOD, n_positional, n_keyword,
                          star_flags)

    def return_value(self):
        self._op(-1, O.MP_BC_RETURN_VALUE)
        self.suppress = True

    def raise_varargs(self, n_args):
        if n_args > 2:
            raise EmitError("raise takes at most two arguments")
        self._op(-n_args, O.MP_BC_RAISE_LAST + n_args)
        self.suppress = True

    def yield_(self, kind):
        self._op(-kind, O.MP_BC_YIELD_VALUE + kind)
        self.scope.scope_flags |= S.MP_SCOPE_FLAG_GENERATOR
