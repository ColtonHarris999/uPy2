"""The tokenizer: a port of ``py/lexer.c`` from MicroPython v1.29.0.

Structured like the original so the two can be read side by side: a Lexer
carries the current token in ``tok_kind`` / ``tok_line`` / ``tok_column`` /
``vstr`` and ``to_next()`` advances, rather than returning token objects.  That
is also the cheapest shape for the heap budget -- no object per token.

The character reader keeps three bytes of lookahead (``chr0``, ``chr1``,
``chr2``), because the longest decision the lexer makes needs that much: a
string prefix like ``rb'``.  EOF is byte 0, and a byte 0 in the input becomes
byte 1 so it cannot be mistaken for EOF, exactly as in C.

f-strings are rewritten *in the lexer*, as MicroPython does: ``f"{x!r:>{w}}"``
becomes the token stream for ``"{!r:>{}}".format((x),(w),)``.  The replacement
text is pushed through ``inject_chrs`` and re-lexed, so the parser never learns
that f-strings exist.  This is also why the mechanism is unaware of Python
syntax and cannot handle an expression containing ``}`` or ``:`` -- a
MicroPython limitation that must be reproduced, not fixed.

Nothing here recurses: the target bans it and the frame budget is 32.
"""

from .tokens import (  # noqa: F401
    MP_TOKEN_END, MP_TOKEN_INVALID, MP_TOKEN_DEDENT_MISMATCH,
    MP_TOKEN_LONELY_STRING_OPEN, MP_TOKEN_MALFORMED_FSTRING,
    MP_TOKEN_NEWLINE, MP_TOKEN_INDENT, MP_TOKEN_DEDENT,
    MP_TOKEN_NAME, MP_TOKEN_INTEGER, MP_TOKEN_FLOAT_OR_IMAG,
    MP_TOKEN_STRING, MP_TOKEN_BYTES, MP_TOKEN_ELLIPSIS,
    MP_TOKEN_KW_FALSE, MP_TOKEN_KW_TRUE, MP_TOKEN_KW___DEBUG__,
    MP_TOKEN_OP_NOT_EQUAL, MP_TOKEN_DEL_PERIOD,
    MP_TOKEN_DEL_PAREN_OPEN, MP_TOKEN_DEL_PAREN_CLOSE,
    MP_TOKEN_DEL_BRACKET_OPEN, MP_TOKEN_DEL_BRACKET_CLOSE,
    MP_TOKEN_DEL_BRACE_OPEN, MP_TOKEN_DEL_BRACE_CLOSE,
    keyword_kind, token_name,
)
from . import tokens as _t

TAB_SIZE = 8
LEXER_EOF = 0
LEXER_INVALID_BYTE = 1


class LexerError(Exception):
    """Raised where the C lexer raises rather than setting a token kind."""


class NotImplementedFeature(LexerError):
    """A construct the C lexer implements but this port does not yet."""


class NotImplementedByMicroPython(LexerError):
    """A construct the *pinned MicroPython* does not implement either.

    Raising here is the port being faithful rather than a gap: ``py/lexer.c``
    calls ``mp_raise_NotImplementedError`` at the same point, with the same
    message, and ``mpy-cross`` exits non-zero on such a file.
    """


# -- character classes ----------------------------------------------------
#
# MicroPython's unichar_* helpers are ASCII-only; bytes >= 0x80 are admitted
# to identifiers by a separate test, which is why is_letter must not accept
# them.

def _is_space(c):
    return c == 0x20 or 0x09 <= c <= 0x0D


def _is_alpha(c):
    return (0x41 <= c <= 0x5A) or (0x61 <= c <= 0x7A)


def _is_digit(c):
    return 0x30 <= c <= 0x39


def _is_xdigit(c):
    return _is_digit(c) or (0x41 <= c <= 0x46) or (0x61 <= c <= 0x66)


def _xdigit_value(c):
    if _is_digit(c):
        return c - 0x30
    return (c | 0x20) - 0x61 + 10


def _utf8_encode(code, out):
    """Append *code* to bytearray *out* as UTF-8, like vstr_add_char."""
    if code < 0x80:
        out.append(code)
    elif code < 0x800:
        out.append(0xC0 | (code >> 6))
        out.append(0x80 | (code & 0x3F))
    elif code < 0x10000:
        out.append(0xE0 | (code >> 12))
        out.append(0x80 | ((code >> 6) & 0x3F))
        out.append(0x80 | (code & 0x3F))
    else:
        out.append(0xF0 | (code >> 18))
        out.append(0x80 | ((code >> 12) & 0x3F))
        out.append(0x80 | ((code >> 6) & 0x3F))
        out.append(0x80 | (code & 0x3F))


# The operator encoding from py/lexer.c.  In tok_enc, a bare character starts
# an operator, 'e' means "if the next character matches, end here", and 'c'
# means "if it matches, continue matching".
TOK_ENC = (
    "()[]{},;~"      # singles
    ":e="            # :  :=
    "<e=c<e="        # <  <=  <<  <<=
    ">e=c>e="        # >  >=  >>  >>=
    "*e=c*e="        # *  *=  **  **=
    "+e="            # +  +=
    "-e=e>"          # -  -=  ->
    "&e="            # &  &=
    "|e="            # |  |=
    "/e=c/e="        # /  /=  //  //=
    "%e="            # %  %=
    "^e="            # ^  ^=
    "@e="            # @  @=
    "=e="            # =  ==
    "!."             # special cases: != . ...
)

TOK_ENC_KIND = (
    _t.MP_TOKEN_DEL_PAREN_OPEN, _t.MP_TOKEN_DEL_PAREN_CLOSE,
    _t.MP_TOKEN_DEL_BRACKET_OPEN, _t.MP_TOKEN_DEL_BRACKET_CLOSE,
    _t.MP_TOKEN_DEL_BRACE_OPEN, _t.MP_TOKEN_DEL_BRACE_CLOSE,
    _t.MP_TOKEN_DEL_COMMA, _t.MP_TOKEN_DEL_SEMICOLON, _t.MP_TOKEN_OP_TILDE,

    _t.MP_TOKEN_DEL_COLON, _t.MP_TOKEN_OP_ASSIGN,
    _t.MP_TOKEN_OP_LESS, _t.MP_TOKEN_OP_LESS_EQUAL,
    _t.MP_TOKEN_OP_DBL_LESS, _t.MP_TOKEN_DEL_DBL_LESS_EQUAL,
    _t.MP_TOKEN_OP_MORE, _t.MP_TOKEN_OP_MORE_EQUAL,
    _t.MP_TOKEN_OP_DBL_MORE, _t.MP_TOKEN_DEL_DBL_MORE_EQUAL,
    _t.MP_TOKEN_OP_STAR, _t.MP_TOKEN_DEL_STAR_EQUAL,
    _t.MP_TOKEN_OP_DBL_STAR, _t.MP_TOKEN_DEL_DBL_STAR_EQUAL,
    _t.MP_TOKEN_OP_PLUS, _t.MP_TOKEN_DEL_PLUS_EQUAL,
    _t.MP_TOKEN_OP_MINUS, _t.MP_TOKEN_DEL_MINUS_EQUAL,
    _t.MP_TOKEN_DEL_MINUS_MORE,
    _t.MP_TOKEN_OP_AMPERSAND, _t.MP_TOKEN_DEL_AMPERSAND_EQUAL,
    _t.MP_TOKEN_OP_PIPE, _t.MP_TOKEN_DEL_PIPE_EQUAL,
    _t.MP_TOKEN_OP_SLASH, _t.MP_TOKEN_DEL_SLASH_EQUAL,
    _t.MP_TOKEN_OP_DBL_SLASH, _t.MP_TOKEN_DEL_DBL_SLASH_EQUAL,
    _t.MP_TOKEN_OP_PERCENT, _t.MP_TOKEN_DEL_PERCENT_EQUAL,
    _t.MP_TOKEN_OP_CARET, _t.MP_TOKEN_DEL_CARET_EQUAL,
    _t.MP_TOKEN_OP_AT, _t.MP_TOKEN_DEL_AT_EQUAL,
    _t.MP_TOKEN_DEL_EQUAL, _t.MP_TOKEN_OP_DBL_EQUAL,
)

_OPEN_BRACKETS = (MP_TOKEN_DEL_PAREN_OPEN, MP_TOKEN_DEL_BRACKET_OPEN,
                  MP_TOKEN_DEL_BRACE_OPEN)
_CLOSE_BRACKETS = (MP_TOKEN_DEL_PAREN_CLOSE, MP_TOKEN_DEL_BRACKET_CLOSE,
                   MP_TOKEN_DEL_BRACE_CLOSE)

_SIMPLE_ESCAPES = {
    0x61: 0x07,  # \a
    0x62: 0x08,  # \b
    0x74: 0x09,  # \t
    0x6E: 0x0A,  # \n
    0x76: 0x0B,  # \v
    0x66: 0x0C,  # \f
    0x72: 0x0D,  # \r
}


class Lexer:
    """The tokenizer.  Construct it, then read ``tok_kind`` and call
    ``to_next()``; the first token is already available after construction,
    as in C."""

    def __init__(self, src_name, source, optimise_value=0):
        if isinstance(source, str):
            source = source.encode("utf-8")
        self.src_name = src_name
        self._src = source
        self._pos = 0
        # mp_optimise_value: with -O or above, __debug__ lexes as False.
        self.optimise_value = optimise_value

        self.line = 1
        self.column = 1
        self.emit_dent = 0
        self.nested_bracket_level = 0
        self.indent_level = [0]

        self.tok_line = 0
        self.tok_column = 0
        self.tok_kind = MP_TOKEN_END
        self.vstr = bytearray()

        # f-string state: characters being re-lexed, and the .format() call
        # being accumulated.
        self.inject_chrs = bytearray()
        self.inject_chrs_idx = 0
        self.fstring_args = bytearray()

        # Prime the three-byte window.  C does this by calling next_char
        # three times from a zeroed lexer, which also means a leading
        # newline is synthesised for an empty file.
        self.chr0 = LEXER_EOF
        self.chr1 = LEXER_EOF
        self.chr2 = LEXER_EOF
        self._next_char()
        self._next_char()
        self._next_char()
        # C starts at line 1 column 1 after priming.
        self.line = 1
        self.column = 1
        self.to_next()

    # -- the character window --------------------------------------------

    def _read_byte(self):
        """One byte from the injection buffer or the source; EOF -> 0."""
        if self.inject_chrs_idx:
            c = self.inject_chrs[self.inject_chrs_idx]
            self.inject_chrs_idx += 1
            if self.inject_chrs_idx >= len(self.inject_chrs):
                self.inject_chrs = bytearray()
                self.inject_chrs_idx = 0
            return c
        if self._pos >= len(self._src):
            return LEXER_EOF
        c = self._src[self._pos]
        self._pos += 1
        # A real 0 byte must not read as EOF.
        if c == LEXER_EOF:
            return LEXER_INVALID_BYTE
        return c

    def _next_char(self):
        if self.chr0 == 0x0A:
            self.line += 1
            self.column = 1
        elif self.chr0 == 0x09:
            self.column = (((self.column - 1 + TAB_SIZE) // TAB_SIZE)
                           * TAB_SIZE) + 1
        else:
            self.column += 1

        self.chr0 = self.chr1
        self.chr1 = self.chr2

        while True:
            chr2 = self._read_byte()
            if self.chr1 == 0x0D:
                # CR becomes LF; CR LF collapses to one newline.
                self.chr1 = 0x0A
                if chr2 == 0x0A:
                    continue
            break

        # A file not ending in a newline gets one.
        if chr2 == LEXER_EOF and self.chr1 != LEXER_EOF and self.chr1 != 0x0A:
            chr2 = 0x0A
        self.chr2 = chr2

    # -- small predicates -------------------------------------------------

    def _is_end(self):
        return self.chr0 == LEXER_EOF

    def _is_char(self, c):
        return self.chr0 == c

    def _is_head_of_identifier(self):
        return _is_alpha(self.chr0) or self.chr0 == 0x5F or self.chr0 >= 0x80

    def _is_tail_of_identifier(self):
        return self._is_head_of_identifier() or _is_digit(self.chr0)

    def _is_string_or_bytes(self):
        c0, c1, c2 = self.chr0, self.chr1, self.chr2
        if c0 == 0x27 or c0 == 0x22:  # ' "
            return True
        quote1 = c1 == 0x27 or c1 == 0x22
        # r u b f t followed by a quote
        if quote1 and c0 in (0x72, 0x75, 0x62, 0x66, 0x74):
            return True
        quote2 = c2 == 0x27 or c2 == 0x22
        if quote2:
            pair = (c0, c1)
            # rf fr rt tr rb br
            if pair in ((0x72, 0x66), (0x66, 0x72), (0x72, 0x74),
                        (0x74, 0x72), (0x72, 0x62), (0x62, 0x72)):
                return True
        return False

    # -- whitespace, comments, continuations ------------------------------

    def _skip_whitespace(self, stop_at_newline):
        """True if a physical newline was reached (always true when
        *stop_at_newline* and not inside brackets)."""
        while not self._is_end():
            if self.chr0 == 0x0A:
                if stop_at_newline and self.nested_bracket_level == 0:
                    return True
                self._next_char()
            elif _is_space(self.chr0):
                self._next_char()
            elif self.chr0 == 0x23:  # '#'
                self._next_char()
                while not self._is_end() and self.chr0 != 0x0A:
                    self._next_char()
                # the newline is handled on the next pass
            elif self.chr0 == 0x5C and self.chr1 == 0x0A:  # backslash newline
                self._next_char()
                self._next_char()
            else:
                break
        return False

    # -- string literals --------------------------------------------------

    def _get_hex(self, num_digits):
        """Read *num_digits* hex digits; returns (ok, value)."""
        num = 0
        while num_digits > 0:
            num_digits -= 1
            self._next_char()
            c = self.chr0
            if not _is_xdigit(c):
                return False, 0
            num = (num << 4) + _xdigit_value(c)
        return True, num

    def _parse_string_literal(self, is_raw, is_fstring, is_tstring):
        quote_char = 0x27
        if self._is_char(0x22):
            quote_char = 0x22
        self._next_char()

        if self.chr0 == quote_char and self.chr1 == quote_char:
            self._next_char()
            self._next_char()
            num_quotes = 3
        else:
            num_quotes = 1

        if is_fstring and len(self.fstring_args) == 0:
            # Adjacent f-strings share one .format() call, so only open it
            # when this is the first.
            self.fstring_args.extend(b".format(")
        if is_tstring and len(self.fstring_args) == 0:
            # A t-string becomes a call whose first argument is the tuple of
            # literal pieces, so open both the tuple and its first piece.
            self.vstr.append(0x28)  # '('
            self.vstr.append(0x28)  # '('
            for _ in range(num_quotes):
                self.vstr.append(quote_char)

        # Per-literal t-string state.  _tstr_nest is the brace depth inside a
        # format spec, counted so that a spec containing braces of its own --
        # which is a nested f-string -- is passed through whole.
        self._tstr_nest = 0
        self._tstr_fmt_index = 0
        self._tstr_needs_f = False
        interpolations = 0

        n_closing = 0
        vstr = self.vstr
        args = self.fstring_args
        while (not self._is_end()
               and (num_quotes > 1 or self.chr0 != 0x0A)
               and n_closing < num_quotes):
            if self._is_char(quote_char):
                n_closing += 1
                vstr.append(self.chr0)
            elif is_tstring and self._is_char(0x0A):
                # A multi-line t-string is re-lexed as a single-quoted
                # string, so its newlines go in as escapes.
                vstr.append(0x5C)
                vstr.append(0x6E)  # 'n'
            else:
                n_closing = 0
                if (is_fstring or is_tstring) and self._is_char(0x7B):  # '{'
                    if not self._tstr_nest:
                        self._parse_fstring_arg(quote_char, is_tstring,
                                                num_quotes)
                        continue
                    # Inside a format spec: count the brace and let the plain
                    # character handling below copy it.
                    self._tstr_nest += 1

                if is_tstring and self._is_char(0x5C):  # backslash
                    # Left for the re-lex to interpret, so the escape is
                    # copied rather than resolved.
                    vstr.append(0x5C)
                    if is_raw:
                        vstr.append(0x5C)
                    else:
                        self._next_char()
                        vstr.append(self.chr0)
                elif self._is_char(0x5C):
                    if self._parse_escape(is_raw, vstr):
                        continue
                elif (is_tstring and self._tstr_nest
                        and self._is_char(0x7D)):  # '}'
                    self._tstr_nest -= 1
                    if self._tstr_nest > 0:
                        self._tstr_needs_f = True
                        vstr.append(self.chr0)
                    else:
                        # The interpolation is complete: move the format spec
                        # out of the literal piece and into the argument list.
                        interpolations += 1
                        if self._tstr_needs_f:
                            args.append(0x66)  # 'f'
                            self._tstr_needs_f = False
                        args.append(quote_char)
                        args.extend(vstr[self._tstr_fmt_index + 1:])
                        vstr[self._tstr_fmt_index:] = b""
                        args.append(quote_char)
                        args.append(0x2C)  # ','
                else:
                    vstr.append(self.chr0)
                    if is_tstring and self.chr0 == 0x7D:
                        if self.chr1 == 0x7D:
                            self._next_char()   # '}}' is one '}'
                        else:
                            self.tok_kind = MP_TOKEN_MALFORMED_FSTRING
            self._next_char()

        if n_closing < num_quotes:
            self.tok_kind = MP_TOKEN_LONELY_STRING_OPEN

        if is_tstring:
            if self._tstr_nest > 0:
                self.tok_kind = MP_TOKEN_MALFORMED_FSTRING
            if 1 + interpolations * 4 > 255:
                # More arguments than a call can take, so pass the
                # interpolations as one tuple instead.
                args[0:0] = b"("
                args.append(0x29)  # ')'
            # The quotes stay: this text is going to be re-lexed.
            return

        # Drop the closing quotes from the token text.  MicroPython's
        # bytearray has no item deletion, so truncate by slice assignment.
        if n_closing:
            vstr[len(vstr) - n_closing:] = b""

    def _parse_fstring_arg(self, quote_char, is_tstring, num_quotes):
        """Handle a ``{`` inside an f-string or t-string.

        The caller continues its loop without the trailing ``next_char``,
        matching the C ``goto continue_outer``.
        """
        self._next_char()
        if self._is_char(0x7B):  # '{{'
            if not is_tstring:
                # passes through to str.format
                self.vstr.append(0x7B)
            self._next_char()
            self.vstr.append(0x7B)
            return

        args = self.fstring_args
        args.append(0x28)  # '('
        start = len(args)
        # Copy the expression until the bracket level is zero and we reach a
        # conversion '!', a format spec ':', or the closing '}'.  '!' only
        # counts when followed by r or s and then ':' or '}', so that '!='
        # still works inside the expression.
        depth = 0
        while not self._is_end():
            c = self.chr0
            if depth == 0:
                if c == 0x3A or c == 0x7D:  # ':' '}'
                    break
                if (c == 0x21  # '!'
                        and self.chr1 in (0x72, 0x73)
                        and self.chr2 in (0x3A, 0x7D)):
                    break
            if c == 0x5B or c == 0x7B:  # '[' '{'
                depth += 1
            elif c == 0x5D or c == 0x7D:  # ']' '}'
                depth -= 1
            args.append(c)
            self._next_char()

        was_debug = False
        if len(args) and args[len(args) - 1] == 0x3D:  # '='
            # f'{a=}' keeps the expression text in the format string.
            self.vstr.extend(args[start:])
            args[len(args) - 1:] = b""
            was_debug = True

        if is_tstring:
            # The expression text becomes a string literal, so trailing
            # whitespace would show up in it.
            while len(args) and _is_space(args[len(args) - 1]):
                args[len(args) - 1:] = b""

        if len(args) == start:
            # empty interpolation, eg f'{}'
            self.tok_kind = MP_TOKEN_MALFORMED_FSTRING

        args.append(0x29)  # ')'
        args.append(0x2C)  # ','

        if is_tstring:
            # Each interpolation contributes four arguments: its value, its
            # source text, its conversion and its format spec.  The value is
            # already in; the source text is the same characters quoted.
            args.append(quote_char)
            n = len(args) - start - 3
            for j in range(n):
                b = args[start + j]
                if b == quote_char or b == 0x5C:
                    args.append(0x5C)
                args.append(b)
            args.append(quote_char)
            args.append(0x2C)

            # End this literal piece and start the next one.
            for _ in range(num_quotes):
                self.vstr.append(quote_char)
            self.vstr.append(0x2C)  # ','
            for _ in range(num_quotes):
                self.vstr.append(quote_char)

            if self._is_char(0x21):  # '!r' or '!s'
                self._next_char()
                args.append(quote_char)
                args.append(self.chr0)
                self._next_char()
                args.append(quote_char)
                args.append(0x2C)
            elif was_debug and not self._is_char(0x3A):
                # t'{a=}' repeats the value with repr, as f'{a=}' does.
                args.extend(b"'r',")
            else:
                args.extend(b"None,")

            if self._is_char(0x3A):  # ':'
                self._next_char()
            # From here to the closing '}' is the format spec, accumulated in
            # vstr and moved into args when that '}' arrives.
            self._tstr_nest = 1
            self._tstr_fmt_index = len(self.vstr)

        self.vstr.append(0x7B)  # '{'

    def _parse_escape(self, is_raw, vstr):
        """Handle a backslash in a string.  Returns True to skip the
        caller's trailing ``next_char`` (a line continuation)."""
        self._next_char()
        c = self.chr0
        if is_raw:
            # Raw strings may escape a quote, but keep the backslash too.
            vstr.append(0x5C)
        else:
            if c == 0x0A:
                # escaped newline: drop it
                self._next_char()
                return True
            if c in (0x5C, 0x27, 0x22):
                pass
            elif c in _SIMPLE_ESCAPES:
                c = _SIMPLE_ESCAPES[c]
            elif c in (0x75, 0x55, 0x78):  # u U x
                if c in (0x75, 0x55) and self.tok_kind == MP_TOKEN_BYTES:
                    # b'ሴ' == b'\\u1234'
                    vstr.append(0x5C)
                else:
                    n = 2 if c == 0x78 else (4 if c == 0x75 else 8)
                    ok, num = self._get_hex(n)
                    if not ok:
                        self.tok_kind = MP_TOKEN_INVALID
                    c = num
                    self._emit_escaped(c, vstr)
                    return False
            elif c == 0x4E:  # '\N{...}'
                # '\N{LATIN SMALL LETTER A}' would need the whole Unicode
                # name table in the core, which py/lexer.c declines to carry.
                raise NotImplementedByMicroPython("unicode name escapes")
            elif 0x30 <= c <= 0x37:  # octal, 1-3 digits
                digits = 3
                num = c - 0x30
                while 0x30 <= self.chr1 <= 0x37:
                    digits -= 1
                    if digits == 0:
                        break
                    self._next_char()
                    num = num * 8 + (self.chr0 - 0x30)
                c = num
            else:
                # Unrecognised escape: CPython passes the backslash through.
                vstr.append(0x5C)
            self._emit_escaped(c, vstr)
            return False
        vstr.append(c)
        return False

    def _emit_escaped(self, c, vstr):
        """Append an escape's resulting character, with the unicode build's
        rules: a str takes any code point, bytes only a single byte."""
        if c < 0x110000 and self.tok_kind == MP_TOKEN_STRING:
            _utf8_encode(c, vstr)
        elif c < 0x100 and self.tok_kind == MP_TOKEN_BYTES:
            vstr.append(c)
        else:
            self.tok_kind = MP_TOKEN_INVALID

    def _lex_string_or_bytes(self):
        """Accumulate one or more adjacent string/bytes literals.

        Concatenation happens here rather than in the parser so one buffer
        can hold the result, as the C comment explains.
        """
        # MP_TOKEN_END marks "no literal seen yet".
        self.tok_kind = MP_TOKEN_END
        had_tstring = False
        while True:
            is_raw = False
            is_fstring = False
            is_tstring = False
            kind = MP_TOKEN_STRING
            n_char = 0
            c0, c1 = self.chr0, self.chr1
            if c0 == 0x75:  # u
                n_char = 1
            elif c0 == 0x62:  # b
                kind = MP_TOKEN_BYTES
                n_char = 1
                if c1 == 0x72:
                    is_raw = True
                    n_char = 2
            elif c0 == 0x72:  # r
                is_raw = True
                n_char = 1
                if c1 == 0x62:
                    kind = MP_TOKEN_BYTES
                    n_char = 2
                elif c1 == 0x66:
                    is_fstring = True
                    n_char = 2
                elif c1 == 0x74:
                    is_tstring = True
                    n_char = 2
            elif c0 == 0x66:  # f
                is_fstring = True
                n_char = 1
                if c1 == 0x72:
                    is_raw = True
                    n_char = 2
            elif c0 == 0x74:  # t
                is_tstring = True
                n_char = 1
                if c1 == 0x72:
                    is_raw = True
                    n_char = 2

            if is_tstring:
                had_tstring = True

            if self.tok_kind == MP_TOKEN_END:
                self.tok_kind = kind
            elif self.tok_kind != kind:
                # str and bytes cannot be concatenated
                break

            if n_char:
                self._next_char()
                if n_char == 2:
                    self._next_char()

            self._parse_string_literal(is_raw, is_fstring, is_tstring)
            self._skip_whitespace(True)
            if not self._is_string_or_bytes():
                break

        if had_tstring:
            # Close the tuple of literal pieces and put it in front of the
            # interpolations, so the whole thing reads as the argument list of
            # a call: __template__(("a", "b",), (x), 'x', None, "",).  The
            # token itself becomes that callable's name, and the arguments are
            # spliced into the input below.
            self.vstr.append(0x2C)  # ','
            self.vstr.append(0x29)  # ')'
            self.vstr.append(0x2C)  # ','
            self.fstring_args[0:0] = self.vstr
            if self.tok_kind > MP_TOKEN_MALFORMED_FSTRING:
                self.tok_kind = MP_TOKEN_NAME
                self.vstr = bytearray(b"__template__")

        if len(self.fstring_args):
            # The f-string is complete: splice ".format(...)" into the input
            # ahead of the current window and carry on lexing from there.
            self.fstring_args.append(0x29)  # ')'
            if self.inject_chrs_idx == 0:
                self.inject_chrs = bytearray()
                self.inject_chrs.append(self.chr0)
                self.inject_chrs.append(self.chr1)
                self.inject_chrs.append(self.chr2)
                at = 0
            else:
                # Already injecting: rewind over the cached window.
                self.inject_chrs_idx -= 3
                at = self.inject_chrs_idx
            self.inject_chrs[at:at] = self.fstring_args
            self.fstring_args = bytearray()
            self.chr0 = self.inject_chrs[at]
            self.chr1 = self.inject_chrs[at + 1]
            self.chr2 = self.inject_chrs[at + 2]
            self.inject_chrs_idx = at + 3

    # -- names, numbers, operators ---------------------------------------

    def _lex_name(self):
        self.tok_kind = MP_TOKEN_NAME
        vstr = self.vstr
        vstr.append(self.chr0)
        self._next_char()
        while not self._is_end() and self._is_tail_of_identifier():
            vstr.append(self.chr0)
            self._next_char()
        try:
            text = bytes(vstr).decode("utf-8")
        except UnicodeError:
            return
        kind = keyword_kind(text)
        if kind is not None:
            self.tok_kind = kind
            if kind == MP_TOKEN_KW___DEBUG__:
                # Resolved here so the parser rejects things like x.__debug__
                # and the compiler need not special-case it.
                self.tok_kind = (MP_TOKEN_KW_TRUE if self.optimise_value == 0
                                 else MP_TOKEN_KW_FALSE)

    def _lex_number(self):
        forced_integer = False
        if self._is_char(0x2E):  # leading '.'
            self.tok_kind = MP_TOKEN_FLOAT_OR_IMAG
        else:
            self.tok_kind = MP_TOKEN_INTEGER
            if self.chr0 == 0x30 and (self.chr1 | 0x20) in (0x62, 0x6F, 0x78):
                forced_integer = True  # 0b 0o 0x

        vstr = self.vstr
        vstr.append(self.chr0)
        self._next_char()
        while not self._is_end():
            c = self.chr0
            if not forced_integer and (c == 0x65 or c == 0x45):  # e E
                self.tok_kind = MP_TOKEN_FLOAT_OR_IMAG
                vstr.append(0x65)
                self._next_char()
                if self.chr0 == 0x2B or self.chr0 == 0x2D:
                    vstr.append(self.chr0)
                    self._next_char()
            elif _is_alpha(c) or _is_digit(c) or c == 0x2E:
                if c in (0x2E, 0x6A, 0x4A):  # . j J
                    self.tok_kind = MP_TOKEN_FLOAT_OR_IMAG
                vstr.append(c)
                self._next_char()
            elif c == 0x5F:  # '_' separators are dropped
                self._next_char()
            else:
                break

    def _lex_operator(self):
        enc = TOK_ENC
        i = 0
        index = 0
        n = len(enc)
        while i < n and ord(enc[i]) != self.chr0:
            if enc[i] == "e" or enc[i] == "c":
                i += 1
            i += 1
            index += 1
        self._next_char()

        if i >= n:
            self.tok_kind = MP_TOKEN_INVALID
            return
        ch = enc[i]
        if ch == "!":
            # '!' alone is not an operator.
            if self._is_char(0x3D):
                self._next_char()
                self.tok_kind = MP_TOKEN_OP_NOT_EQUAL
            else:
                self.tok_kind = MP_TOKEN_INVALID
            return
        if ch == ".":
            # '.' and '...' exist, '..' does not.
            if self.chr0 == 0x2E and self.chr1 == 0x2E:
                self._next_char()
                self._next_char()
                self.tok_kind = MP_TOKEN_ELLIPSIS
            else:
                self.tok_kind = MP_TOKEN_DEL_PERIOD
            return

        # Extend the match as far as the encoding allows.
        i += 1
        t_index = index
        while i < n and (enc[i] == "c" or enc[i] == "e"):
            t_index += 1
            if i + 1 < n and ord(enc[i + 1]) == self.chr0:
                self._next_char()
                index = t_index
                if enc[i] == "e":
                    break
            elif enc[i] == "c":
                break
            i += 2

        self.tok_kind = TOK_ENC_KIND[index]
        if self.tok_kind in _OPEN_BRACKETS:
            self.nested_bracket_level += 1
        elif self.tok_kind in _CLOSE_BRACKETS:
            self.nested_bracket_level -= 1

    # -- the main dispatcher ----------------------------------------------

    def to_next(self):
        """Advance to the next token."""
        self.vstr = bytearray()

        had_physical_newline = self._skip_whitespace(True)

        self.tok_line = self.line
        self.tok_column = self.column

        if self.emit_dent < 0:
            self.tok_kind = MP_TOKEN_DEDENT
            self.emit_dent += 1
        elif self.emit_dent > 0:
            self.tok_kind = MP_TOKEN_INDENT
            self.emit_dent -= 1
        elif had_physical_newline:
            # At the end of a line: consume the blank lines and comments that
            # follow, then decide indentation from the column we land on.
            self._skip_whitespace(False)
            self.tok_kind = MP_TOKEN_NEWLINE
            num_spaces = self.column - 1
            top = self.indent_level[len(self.indent_level) - 1]
            if num_spaces == top:
                pass
            elif num_spaces > top:
                self.indent_level.append(num_spaces)
                self.emit_dent += 1
            else:
                while num_spaces < self.indent_level[len(self.indent_level) - 1]:
                    self.indent_level.pop()
                    self.emit_dent -= 1
                if num_spaces != self.indent_level[len(self.indent_level) - 1]:
                    self.tok_kind = MP_TOKEN_DEDENT_MISMATCH
        elif self._is_end():
            self.tok_kind = MP_TOKEN_END
        elif self._is_string_or_bytes():
            self._lex_string_or_bytes()
        elif self._is_head_of_identifier():
            self._lex_name()
        elif _is_digit(self.chr0) or (self.chr0 == 0x2E and _is_digit(self.chr1)):
            self._lex_number()
        else:
            self._lex_operator()

    # -- convenience ------------------------------------------------------

    def text(self):
        """The current token's text as bytes."""
        return bytes(self.vstr)

    def describe(self):
        return "%s@%d:%d %r" % (token_name(self.tok_kind), self.tok_line,
                                self.tok_column, bytes(self.vstr))
