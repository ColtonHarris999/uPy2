"""How a float constant is spelled in an ``.mpy``.

``save_obj`` stores a float as text, so the spelling is part of the file.
``py/objfloat.c`` prints one with ``mp_print_float(..., 'g',
PF_FLAG_ALWAYS_DECIMAL, ..., MP_FLOAT_REPR_PREC)``, and in
``py/formatfloat.c`` that magic precision means: render with **16**
significant digits, and only if the result does not read back as the same
double, use more.  So the spelling is the shortest round-tripping form *at or
above* 16 digits, not the shortest overall -- which is why MicroPython prints
``8.888000000000001e+32`` where CPython prints ``8.888e+32``.

The arithmetic here is exact and does its own, so that the same source
compiles to the same bytes whatever Python is running the compiler.  Neither
``"%.17e"`` nor ``float(text)`` can be used for this: both are the *runtime's*
conversions, MicroPython's are not correctly rounded, and a compiler whose
output depends on its host is not a compiler.  Exactness is not optional
either -- the question "do 16 digits read back as this double?" is decided by
a comparison against a half-unit-in-the-last-place boundary, and an
approximate answer picks the wrong number of digits.

Doing it exactly needs integers wider than the target has: a subnormal's
decimal expansion is some 750 digits.  So ``Big`` below is a small
fixed-purpose multiprecision integer over 30-bit limbs in an ``array('i')``,
with only the four operations this file needs -- multiply by a small integer,
divide by a small integer, compare, and read out decimal digits.  Every
intermediate stays inside a signed 64-bit value, which is the widest integer
the target has.

Sixteen or seventeen digits is the whole range: seventeen always round-trips
for an IEEE double, so the search is one test, not a loop.  Measured over
5631 doubles, 3518 need 16 and 2113 need 17; none needs more.
"""

from array import array

# 30 bits a limb, so that a limb times a small multiplier, and a remainder
# times the base plus a limb, both stay well inside a signed 64-bit value:
# 2^30 * 10^4 is about 1.1e13.
_LIMB_BITS = 30
_LIMB_BASE = 1 << _LIMB_BITS
_LIMB_MASK = _LIMB_BASE - 1

# The largest small multiplier or divisor passed to Big, which is what bounds
# those intermediates.
_SMALL_MAX = 10000
_DIGIT_CHUNK = 10000   # 10^4, four decimal digits at a time
_DIGIT_CHUNK_LEN = 4

_INF = float("inf")


class Big:
    """A non-negative multiprecision integer, little-endian in 30-bit limbs."""

    def __init__(self, value=0):
        self.limbs = array('i')
        while value:
            self.limbs.append(value & _LIMB_MASK)
            value >>= _LIMB_BITS

    def is_zero(self):
        return len(self.limbs) == 0

    def mul_small(self, m):
        """Multiply by *m*, which must be at most _SMALL_MAX."""
        if m == 1:
            return
        limbs = self.limbs
        carry = 0
        i = 0
        n = len(limbs)
        while i < n:
            t = limbs[i] * m + carry
            limbs[i] = t & _LIMB_MASK
            carry = t >> _LIMB_BITS
            i += 1
        while carry:
            limbs.append(carry & _LIMB_MASK)
            carry >>= _LIMB_BITS

    def divmod_small(self, d):
        """Divide by *d* (at most _SMALL_MAX) in place; return the
        remainder."""
        limbs = self.limbs
        rem = 0
        i = len(limbs) - 1
        while i >= 0:
            # rem < d <= _SMALL_MAX, so this product is bounded.
            cur = rem * _LIMB_BASE + limbs[i]
            limbs[i] = cur // d
            rem = cur % d
            i -= 1
        # Drop the leading zero limbs the division may have left.
        while limbs and limbs[len(limbs) - 1] == 0:
            limbs[len(limbs) - 1:] = array('i')
        return rem

    def mul_pow(self, base, exp):
        """Multiply by ``base ** exp``, in chunks small enough to be safe."""
        if exp <= 0 or self.is_zero():
            return
        # The biggest power of *base* that is still a legal small multiplier.
        step = 1
        per = 0
        while step * base <= _SMALL_MAX:
            step *= base
            per += 1
        while exp >= per:
            self.mul_small(step)
            exp -= per
        chunk = 1
        while exp > 0:
            chunk *= base
            exp -= 1
        self.mul_small(chunk)

    def cmp(self, other):
        """-1, 0 or 1, as this compares less than, equal to or greater."""
        a = self.limbs
        b = other.limbs
        if len(a) != len(b):
            return -1 if len(a) < len(b) else 1
        i = len(a) - 1
        while i >= 0:
            if a[i] != b[i]:
                return -1 if a[i] < b[i] else 1
            i -= 1
        return 0

    def decimal(self):
        """The decimal digits, most significant first, with no leading zero."""
        if self.is_zero():
            return "0"
        parts = []
        while not self.is_zero():
            rem = self.divmod_small(_DIGIT_CHUNK)
            parts.append(rem)
        out = []
        i = len(parts) - 1
        # The most significant chunk is not zero-padded; the rest are.
        out.append("%d" % parts[i])
        i -= 1
        while i >= 0:
            s = "%d" % parts[i]
            out.append("0" * (_DIGIT_CHUNK_LEN - len(s)))
            out.append(s)
            i -= 1
        return "".join(out)


def _decompose(value):
    """``(mantissa, exponent)`` with ``value == mantissa * 2 ** exponent``.

    *value* must be finite and strictly positive.  The bytes are read one at a
    time rather than with ``struct``'s 64-bit codes, which not every build
    has.
    """
    from struct import pack
    b = pack(">d", value)
    biased = ((b[0] & 0x7F) << 4) | (b[1] >> 4)
    frac = b[1] & 0x0F
    for i in range(2, 8):
        frac = (frac << 8) | b[i]
    if biased == 0:
        # Subnormal: no implicit leading one, and a fixed exponent.
        return frac, -1074
    return frac | (1 << 52), biased - 1075


def _exact_digits(mant, exp2):
    """The *exact* decimal digits of ``mant * 2 ** exp2``, and its exponent.

    Exact because the value is a dyadic rational: for a negative exponent,
    multiplying by 5 ** -exp2 turns it into an integer scaled by a power of
    ten, so the digits are that integer's with the point moved.
    """
    n = Big(mant)
    if exp2 >= 0:
        n.mul_pow(2, exp2)
        places = 0
    else:
        n.mul_pow(5, -exp2)
        places = -exp2
    digits = n.decimal()
    # The exponent of the leading digit.
    return digits, len(digits) - 1 - places


def _round_digits(digits, nd):
    """Round *digits* to *nd* significant digits, halves to even.

    Returns ``(digits, carry)``; *carry* is 1 when rounding overflowed into a
    new leading digit, as 9.99 -> 10.0 does, and the caller adjusts the
    exponent by it.
    """
    if len(digits) <= nd:
        return digits + "0" * (nd - len(digits)), 0
    head = digits[:nd]
    first = digits[nd]
    if first > "5":
        up = True
    elif first < "5":
        up = False
    else:
        # Exactly half only if every digit after it is zero; otherwise above.
        up = False
        i = nd + 1
        while i < len(digits):
            if digits[i] != "0":
                up = True
                break
            i += 1
        if not up:
            # A true tie: round to even.
            up = (ord(head[nd - 1]) - 48) % 2 == 1
    if not up:
        return head, 0
    # head is at most 17 digits, so this fits a 64-bit integer.
    s = "%d" % (int(head) + 1)
    if len(s) > nd:
        return s[:nd], 1
    return s, 0


def _cmp_dec_bin(d_mant, d_exp10, b_mant, b_exp2):
    """Compare ``d_mant * 10 ** d_exp10`` with ``b_mant * 2 ** b_exp2``.

    Both sides are scaled up by whatever makes them integers, so the
    comparison is exact.
    """
    a = Big(d_mant)
    b = Big(b_mant)
    if d_exp10 > 0:
        a.mul_pow(10, d_exp10)
    elif d_exp10 < 0:
        b.mul_pow(10, -d_exp10)
    if b_exp2 > 0:
        b.mul_pow(2, b_exp2)
    elif b_exp2 < 0:
        a.mul_pow(2, -b_exp2)
    return a.cmp(b)


def _round_trips(d_mant, d_exp10, mant, exp2):
    """Does the decimal ``d_mant * 10 ** d_exp10`` read back as this double?

    It does when it lies within half a unit in the last place of the double,
    which is the interval the nearest-even rounding of a decimal literal
    would map onto it.  The interval is closed when the double's mantissa is
    even -- a tie rounds to even -- and the gap below is half as wide when the
    double is a power of two, since the mantissa steps down to a finer scale
    there.
    """
    inclusive = (mant % 2) == 0
    # Above: mant + 1/2 ulp.
    cmp_hi = _cmp_dec_bin(d_mant, d_exp10, 2 * mant + 1, exp2 - 1)
    if cmp_hi > 0 or (cmp_hi == 0 and not inclusive):
        return False
    # Below: mant - 1/2 ulp, or a quarter at a power-of-two boundary.
    if mant == (1 << 52) and exp2 > -1074:
        cmp_lo = _cmp_dec_bin(d_mant, d_exp10, 4 * mant - 1, exp2 - 2)
    else:
        cmp_lo = _cmp_dec_bin(d_mant, d_exp10, 2 * mant - 1, exp2 - 1)
    if cmp_lo < 0 or (cmp_lo == 0 and not inclusive):
        return False
    return True


# The 'g' rule switches to an exponent once the decimal exponent reaches
# this.  MicroPython fixes it at 16 for repr; C's %g ties it to the
# precision, which is why %g cannot be used to assemble the text either.
MAX_EXP_ZEROS = 16

SAFE_MANTISSA_DIGITS = 16
# Seventeen significant digits always identify an IEEE double uniquely.
ROUND_TRIP_DIGITS = 17


def float_repr(value):
    """A float as ``save_obj`` writes it."""
    if value != value:
        # mp_format_float never signs a NaN.
        return "nan"
    if value < 0.0:
        sign = "-"
        a = -value
    elif value == 0.0 and _is_neg_zero(value):
        return "-0.0"
    else:
        sign = ""
        a = value
    if a == _INF:
        return sign + "inf"
    if a == 0.0:
        return sign + "0.0"

    mant, exp2 = _decompose(a)
    exact, exp = _exact_digits(mant, exp2)
    digits, carry = _round_digits(exact, SAFE_MANTISSA_DIGITS)
    exp += carry
    if not _round_trips(int(digits), exp - (SAFE_MANTISSA_DIGITS - 1),
                        mant, exp2):
        exp -= carry
        digits, carry = _round_digits(exact, ROUND_TRIP_DIGITS)
        exp += carry

    # 'g' drops trailing zeros after the decimal point.
    i = len(digits)
    while i > 1 and digits[i - 1] == "0":
        i -= 1
    digits = digits[:i]

    if -4 <= exp < MAX_EXP_ZEROS:
        if exp >= 0:
            if len(digits) <= exp + 1:
                out = digits + "0" * (exp + 1 - len(digits))
            else:
                out = digits[:exp + 1] + "." + digits[exp + 1:]
        else:
            out = "0." + "0" * (-exp - 1) + digits
    else:
        out = digits[0]
        if len(digits) > 1:
            out += "." + digits[1:]
        out += "e" + ("+" if exp >= 0 else "-")
        e = exp if exp >= 0 else -exp
        if e >= 100:
            out += "%d" % e
        else:
            out += "%02d" % e
    # PF_FLAG_ALWAYS_DECIMAL: a float always shows a decimal point.
    if "." not in out and "e" not in out:
        out += ".0"
    return sign + out


def _is_neg_zero(value):
    # No math.copysign on every build, and str(-0.0) is "-0.0" everywhere.
    return ("%g" % value)[0] == "-"
