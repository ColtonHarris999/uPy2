"""Bytecode prelude and line-number encoding: part of ``py/emitbc.c``.

Every function's compiled form is one ``fun_data`` blob laid out as
(``py/bc.h``)::

    func signature   bit-interleaved  xSSSSEAA [xFSSKAED ...]
    prelude size     bit-interleaved  xIIIIIIC ...
    source info      simple_name qstr, one qstr per argument, line-number info
    closure section  one byte per cell, its local number
    bytecode

The two interleaved headers are not plain varints: each packs six (or two)
values a few bits at a time so that small functions take fewer bytes.  The
line-number table is a second compression scheme again, alternating two
formats depending on how far the skip is.

This module encodes and decodes all of it.  Decoding is what makes the
encoder testable before the compiler exists: every ``fun_data`` the oracle
produces can be taken apart and put back together, and the bytes must match.
"""

# -- unsigned varints (mp_encode_uint / mp_decode_uint) ------------------
#
# Base-128, most significant group first, continuation bit on all but the
# last byte.  The same shape as the .mpy container's varints.

def encode_uint(n, out):
    if n < 0:
        raise ValueError("cannot encode a negative uint")
    if n < 0x80:
        out.append(n)
        return
    groups = []
    groups.append(n & 0x7F)
    n >>= 7
    while n:
        groups.append(0x80 | (n & 0x7F))
        n >>= 7
    i = len(groups) - 1
    while i >= 0:
        out.append(groups[i])
        i -= 1


def decode_uint(data, pos):
    n = 0
    while True:
        b = data[pos]
        pos += 1
        n = (n << 7) | (b & 0x7F)
        if not (b & 0x80):
            return n, pos


# -- function signature --------------------------------------------------
#
# MP_BC_PRELUDE_SIG_ENCODE: six values interleaved.  S is stored one less than
# its true value, to keep small functions in a single byte.

def encode_prelude_sig(n_state, n_exc_stack, scope_flags, n_pos_args,
                       n_kwonly_args, n_def_pos_args, out):
    S = n_state - 1
    E = n_exc_stack
    F = scope_flags & 0x0F
    A = n_pos_args
    K = n_kwonly_args
    D = n_def_pos_args
    # first byte: xSSSSEAA
    z = ((S & 0x0F) << 3) | ((E & 1) << 2) | (A & 3)
    S >>= 4
    E >>= 1
    A >>= 2
    while S | E | F | A | K | D:
        out.append(0x80 | z)
        # xFSSKAED
        z = (((F & 1) << 6) | ((S & 3) << 4) | ((K & 1) << 3)
             | ((A & 1) << 2) | ((E & 1) << 1) | (D & 1))
        S >>= 2
        E >>= 1
        F >>= 1
        A >>= 1
        K >>= 1
        D >>= 1
    out.append(z)


def decode_prelude_sig(data, pos):
    """Returns ``(n_state, n_exc_stack, scope_flags, n_pos_args,
    n_kwonly_args, n_def_pos_args, new_pos)``."""
    z = data[pos]
    pos += 1
    S = (z >> 3) & 0x0F
    E = (z >> 2) & 0x01
    F = 0
    A = z & 0x03
    K = 0
    D = 0
    n = 0
    while z & 0x80:
        z = data[pos]
        pos += 1
        S |= (z & 0x30) << (2 * n)
        E |= (z & 0x02) << n
        F |= ((z & 0x40) >> 6) << n
        A |= (z & 0x04) << n
        K |= ((z & 0x08) >> 3) << n
        D |= (z & 0x01) << n
        n += 1
    S += 1
    return S, E, F, A, K, D, pos


# -- prelude size --------------------------------------------------------
#
# MP_BC_PRELUDE_SIZE_ENCODE: n_info and n_cells interleaved as xIIIIIIC.

def encode_prelude_size(n_info, n_cell, out):
    I = n_info
    C = n_cell
    while True:
        z = ((I & 0x3F) << 1) | (C & 1)
        C >>= 1
        I >>= 6
        if C | I:
            z |= 0x80
        out.append(z)
        if not (C | I):
            break


def decode_prelude_size(data, pos):
    """Returns ``(n_info, n_cell, new_pos)``."""
    C = 0
    I = 0
    n = 0
    while True:
        z = data[pos]
        pos += 1
        C |= (z & 1) << n
        I |= ((z & 0x7E) >> 1) << (6 * n)
        if not (z & 0x80):
            break
        n += 1
    return I, C, pos


# -- line-number information ---------------------------------------------
#
# emit_write_code_info_bytes_lines: a run of (bytes_to_skip, lines_to_skip)
# deltas, each in one of two formats.

def encode_line_info(bytes_to_skip, lines_to_skip, out):
    """Append one delta, splitting it across entries as needed."""
    if bytes_to_skip == 0 and lines_to_skip == 0:
        raise ValueError("a line-info entry must advance something")
    while bytes_to_skip > 0 or lines_to_skip > 0:
        if lines_to_skip <= 6 or bytes_to_skip > 0x0F:
            # 0LLBBBBB
            b = bytes_to_skip if bytes_to_skip < 0x1F else 0x1F
            if b < bytes_to_skip:
                # no lines can be skipped until all the bytes are
                l = 0
            else:
                l = lines_to_skip if lines_to_skip < 3 else 3
            out.append(b | (l << 5))
        else:
            # 1LLLBBBB LLLLLLLL, with the low bits of l in the second byte
            b = bytes_to_skip if bytes_to_skip < 0x0F else 0x0F
            l = lines_to_skip if lines_to_skip < 0x7FF else 0x7FF
            out.append(0x80 | b | ((l >> 4) & 0x70))
            out.append(l & 0xFF)
        bytes_to_skip -= b
        lines_to_skip -= l


def decode_line_info(data, pos, end):
    """Decode entries until *end*, returning ``[(bytes, lines), ...]``."""
    out = []
    while pos < end:
        z = data[pos]
        pos += 1
        if z & 0x80:
            # 1LLLBBBB LLLLLLLL
            b = z & 0x0F
            l = ((z << 4) & 0x700) | data[pos]
            pos += 1
        else:
            # 0LLBBBBB
            b = z & 0x1F
            l = z >> 5
        out.append((b, l))
    if pos != end:
        raise ValueError("line info overran its section")
    return out, pos


class Prelude:
    """A decoded ``fun_data`` header."""

    def __init__(self, n_state, n_exc_stack, scope_flags, n_pos_args,
                 n_kwonly_args, n_def_pos_args, n_info, n_cell,
                 simple_name, arg_names, line_info, cells, code_offset):
        self.n_state = n_state
        self.n_exc_stack = n_exc_stack
        self.scope_flags = scope_flags
        self.n_pos_args = n_pos_args
        self.n_kwonly_args = n_kwonly_args
        self.n_def_pos_args = n_def_pos_args
        self.n_info = n_info
        self.n_cell = n_cell
        self.simple_name = simple_name      # qstr table index
        self.arg_names = arg_names          # qstr table indices
        self.line_info = line_info          # [(bytes, lines), ...]
        self.cells = cells                  # local numbers, one per cell
        self.code_offset = code_offset      # where the bytecode starts

    def __repr__(self):
        return ("Prelude(n_state=%d, n_exc_stack=%d, flags=%#x, args=%d/%d/%d,"
                " n_info=%d, n_cell=%d, name=q%d)"
                % (self.n_state, self.n_exc_stack, self.scope_flags,
                   self.n_pos_args, self.n_kwonly_args, self.n_def_pos_args,
                   self.n_info, self.n_cell, self.simple_name))


def decode_prelude(data):
    """Take apart a ``fun_data`` blob's header."""
    pos = 0
    sig_start = pos
    (n_state, n_exc_stack, scope_flags, n_pos_args, n_kwonly_args,
     n_def_pos_args, pos) = decode_prelude_sig(data, pos)
    size_start = pos
    n_info, n_cell, pos = decode_prelude_size(data, pos)
    info_start = pos
    # The source info section: the name, one qstr per argument, then the
    # line-number table filling the rest of n_info.
    simple_name, pos = decode_uint(data, pos)
    arg_names = []
    for _ in range(n_pos_args + n_kwonly_args):
        q, pos = decode_uint(data, pos)
        arg_names.append(q)
    info_end = info_start + n_info
    if pos > info_end:
        raise ValueError("prelude names overran the info section")
    line_info, pos = decode_line_info(data, pos, info_end)
    cells = []
    for _ in range(n_cell):
        cells.append(data[pos])
        pos += 1
    return Prelude(n_state, n_exc_stack, scope_flags, n_pos_args,
                   n_kwonly_args, n_def_pos_args, n_info, n_cell,
                   simple_name, arg_names, line_info, cells, pos)


def encode_prelude(p):
    """Rebuild a ``fun_data`` header from a decoded ``Prelude``."""
    out = bytearray()
    encode_prelude_sig(p.n_state, p.n_exc_stack, p.scope_flags, p.n_pos_args,
                       p.n_kwonly_args, p.n_def_pos_args, out)
    encode_prelude_size(p.n_info, p.n_cell, out)
    info = bytearray()
    encode_uint(p.simple_name, info)
    for q in p.arg_names:
        encode_uint(q, info)
    for b, l in p.line_info:
        # Re-emit each entry exactly as decoded: splitting is the encoder's
        # job when building fresh line info, but a decoded entry is already
        # one unit and must round-trip byte for byte.
        _emit_line_entry(b, l, info)
    if len(info) != p.n_info:
        raise ValueError("info section came back %d bytes, expected %d"
                         % (len(info), p.n_info))
    out.extend(info)
    for c in p.cells:
        out.append(c)
    return bytes(out)


def _emit_line_entry(b, l, out):
    """One already-split line-info entry, in whichever form fits.

    The writer picks the short form when it can; a decoded entry has to go
    back out in the same one, so the choice is made from the values exactly
    as emit_write_code_info_bytes_lines would have.
    """
    if l <= 6 or b > 0x0F:
        if b > 0x1F or l > 3:
            raise ValueError("line entry (%d, %d) does not fit the short form"
                             % (b, l))
        out.append(b | (l << 5))
    else:
        if b > 0x0F or l > 0x7FF:
            raise ValueError("line entry (%d, %d) does not fit the long form"
                             % (b, l))
        out.append(0x80 | b | ((l >> 4) & 0x70))
        out.append(l & 0xFF)


# -- instructions --------------------------------------------------------
#
# An opcode's argument shape comes from its high nibble (mpyc/opcodes.py), so
# there is no per-opcode table here either.  Mirrors mp_opcode_decode in
# py/persistentcode.c and the emit_write_bytecode_* family in py/emitbc.c.

from . import opcodes as _O  # noqa: E402


class Instr:
    """One decoded instruction."""

    def __init__(self, opcode, fmt, arg, extra, size, arg_size):
        self.opcode = opcode
        self.fmt = fmt
        self.arg = arg          # None for BYTE-format opcodes
        self.extra = extra      # the trailing byte, or None
        self.size = size        # total bytes, opcode included
        self.arg_size = arg_size  # bytes the argument took

    def __repr__(self):
        return "Instr(%s, arg=%r, extra=%r, size=%d)" % (
            _O.opcode_name(self.opcode), self.arg, self.extra, self.size)


def decode_instruction(data, pos):
    """Decode at *pos*.  Returns ``(Instr, new_pos)``."""
    start = pos
    opcode = data[pos]
    pos += 1
    fmt = _O.opcode_format(opcode)
    arg = None
    arg_start = pos
    if fmt == _O.MP_BC_FORMAT_QSTR or fmt == _O.MP_BC_FORMAT_VAR_UINT:
        arg = data[pos] & 0x7F
        if opcode == _O.MP_BC_LOAD_CONST_SMALL_INT and (arg & 0x40):
            # The only signed argument: sign-extend the first group.  C ORs in
            # ~0 << 7 on an unsigned word; subtracting 0x80 is the same value
            # with Python's arbitrary-precision ints, and the shifts below
            # keep working because << preserves sign.
            arg -= 0x80
        while data[pos] & 0x80:
            pos += 1
            arg = (arg << 7) | (data[pos] & 0x7F)
        pos += 1
    elif fmt == _O.MP_BC_FORMAT_OFFSET:
        if (data[pos] & 0x80) == 0:
            arg = data[pos]
            pos += 1
            if _O.has_signed_offset(opcode):
                arg -= 0x40
        else:
            arg = (data[pos] & 0x7F) | (data[pos + 1] << 7)
            pos += 2
            if _O.has_signed_offset(opcode):
                arg -= 0x4000
    arg_size = pos - arg_start
    extra = None
    if _O.has_extra_byte(opcode):
        extra = data[pos]
        pos += 1
    return Instr(opcode, fmt, arg, extra, pos - start, arg_size), pos


def encode_signed_int(num, out):
    """emit_write_bytecode_byte_int's argument encoding.

    Seven bits per byte, most significant first, with an extra byte added when
    the top group's sign bit would otherwise misreport the sign.
    """
    groups = []
    while True:
        groups.append(num & 0x7F)
        num >>= 7
        if num == 0 or num == -1:
            break
    # The highest stored group's 0x40 bit must match the sign.
    if num == -1 and (groups[-1] & 0x40) == 0:
        groups.append(0x7F)
    elif num == 0 and (groups[-1] & 0x40) != 0:
        groups.append(0)
    i = len(groups) - 1
    while i > 0:
        out.append(groups[i] | 0x80)
        i -= 1
    out.append(groups[0])


def encode_offset(opcode, arg, out, size=None):
    """A jump offset, in one or two bytes.

    *size* forces the width.  The emitter uses one byte whenever the offset
    fits and only ever shrinks across passes, so fresh code can leave it None
    and get the minimal form; re-encoding decoded bytes passes the width that
    was observed, so a wider encoding round-trips unchanged.
    """
    signed = _O.has_signed_offset(opcode)
    if size is None:
        if (signed and -64 <= arg <= 63) or (not signed and 0 <= arg <= 127):
            size = 1
        else:
            size = 2
    if size == 1:
        v = arg + 0x40 if signed else arg
        if not 0 <= v <= 0x7F:
            raise ValueError("offset %d does not fit one byte" % arg)
        out.append(v)
    else:
        v = arg + 0x4000 if signed else arg
        if not 0 <= v <= 0x7FFF:
            raise ValueError("offset %d does not fit two bytes" % arg)
        out.append(0x80 | (v & 0x7F))
        out.append(v >> 7)


def encode_instruction(opcode, arg=None, extra=None, out=None,
                       offset_size=None):
    """Encode one instruction.  Returns the bytearray written to."""
    if out is None:
        out = bytearray()
    out.append(opcode)
    fmt = _O.opcode_format(opcode)
    if fmt == _O.MP_BC_FORMAT_QSTR or fmt == _O.MP_BC_FORMAT_VAR_UINT:
        if arg is None:
            raise ValueError("%s needs an argument" % _O.opcode_name(opcode))
        if opcode == _O.MP_BC_LOAD_CONST_SMALL_INT:
            encode_signed_int(arg, out)
        else:
            encode_uint(arg, out)
    elif fmt == _O.MP_BC_FORMAT_OFFSET:
        if arg is None:
            raise ValueError("%s needs an offset" % _O.opcode_name(opcode))
        encode_offset(opcode, arg, out, offset_size)
    elif arg is not None:
        raise ValueError("%s takes no argument" % _O.opcode_name(opcode))
    if _O.has_extra_byte(opcode):
        if extra is None:
            raise ValueError("%s needs its trailing byte"
                             % _O.opcode_name(opcode))
        out.append(extra)
    elif extra is not None:
        raise ValueError("%s has no trailing byte" % _O.opcode_name(opcode))
    return out


def disassemble(data, start=0, end=None):
    """Yield ``(offset, Instr)`` for a run of bytecode.

    A generator rather than a list: a large function holds thousands of
    instructions, and materialising them all needs tens of kilobytes in one
    allocation -- which the 64 KB budget cannot spare, and which a real
    corpus function was large enough to blow.  Streaming keeps the cost to
    one Instr at a time.
    """
    if end is None:
        end = len(data)
    pos = start
    while pos < end:
        instr, pos = decode_instruction(data, pos)
        yield pos - instr.size, instr
    if pos != end:
        raise ValueError("bytecode overran its section by %d bytes"
                         % (pos - end))
