"""Tests for how a float constant is spelled.

Two properties matter and are checked separately.

**Exactness.** The spelling must read back as the same double, for every
double.  That is checked directly rather than against a table: parse what
``float_repr`` produced and require it to be the value it came from.  It is
also checked against ``mpyc.floatfmt``'s own arithmetic, so that the test
does not simply agree with the runtime's parser about the runtime's parser.

**Determinism.** The same value must give the same text whatever Python is
running, which is why ``floatfmt`` does not use ``"%.17e"`` or
``float(text)``.  Run this file under both runtimes and compare the digest it
prints.

    python3 tests/test_floatfmt.py [--oracle FILE]
    micropython tests/test_floatfmt.py [--oracle FILE]

With ``--oracle``, the file holds one ``repr`` per line for the same values in
the same order, produced by the pinned runtime; the differences are then
classified rather than merely counted (see DEVIATIONS.md, D2).
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

from mpyc import floatfmt as F  # noqa: E402

_fails = []


def check(cond, what):
    if not cond:
        _fails.append(what)
        print("FAIL: %s" % what)


def bits_to_float(bits):
    """A double from its 64 bits, without struct's 64-bit codes."""
    from struct import unpack
    b = bytearray(8)
    i = 7
    while i >= 0:
        b[i] = bits & 0xFF
        bits >>= 8
        i -= 1
    return unpack(">d", bytes(b))[0]


def sample():
    """Awkward values, then a deterministic pseudo-random sweep of bits."""
    for v in (0.0, -0.0, 1.0, -1.0, 0.1, 0.5, 1.0 / 3.0, 1e-5, 1e-4,
              1e15, 1e16, 1e17, 1e22, 1e23, 8.888e32, 1.5e-5, 123456789.0,
              3.141592653589793, 2.718281828459045, 100.0, 1e6, 1e7,
              1.25, 2.5e-10, 1e-300, 1e300, 5e-324,
              1.7976931348623157e308, 2.2250738585072014e-308):
        yield v
    seed = 12345
    n = 0
    while n < 3000:
        seed = (seed * 1103515245 + 12345) & 0xFFFFFFFFFFFFFFFF
        v = bits_to_float(seed)
        n += 1
        if v != v or v == F._INF or v == -F._INF:
            continue
        yield v
    i = 1
    while i < 300:
        yield float(i)
        yield 1.0 / i
        yield i * 1e10
        yield i * 1e-10
        i += 1


def parse_exact(text):
    """The double *text* denotes, decided by exact arithmetic.

    Deliberately not ``float(text)``: the point of the exercise is not to
    depend on the runtime's parser.  The digits and the exponent are compared
    against the candidate double's half-unit boundaries, which is what
    ``_round_trips`` does.
    """
    neg = text[0] == "-"
    if neg:
        text = text[1:]
    mant, _sep, exp_text = text.partition("e")
    exp = int(exp_text) if exp_text else 0
    whole, _dot, frac = mant.partition(".")
    digits = whole + frac
    exp -= len(frac)
    # float() only to produce a *candidate*; exactness is then proved below.
    value = float(text)
    if value == 0.0 or value == F._INF:
        return -value if neg else value
    m, e = F._decompose(value)
    d = int(digits)
    while d != 0 and d % 10 == 0:
        d //= 10
        exp += 1
    if not F._round_trips(d, exp, m, e):
        raise ValueError("%r does not read back as the candidate" % text)
    return -value if neg else value


def test_round_trips():
    n = 0
    for v in sample():
        n += 1
        text = F.float_repr(v)
        if v != v:
            check(text == "nan", "a NaN prints as nan")
            continue
        if v == F._INF or v == -F._INF:
            check(text == "inf" or text == "-inf", "an infinity prints as inf")
            continue
        back = parse_exact(text)
        check(back == v and _same_sign(back, v),
              "%s reads back as the value it came from" % text)
    print("round trip : %d values" % n)


def _same_sign(a, b):
    if a != 0.0:
        return True
    return ("%g" % a)[0] == ("%g" % b)[0]


def test_shapes():
    """The pieces of the 'g' assembly, each one a rule in formatfloat.c."""
    cases = (
        (0.0, "0.0"), (-0.0, "-0.0"), (1.0, "1.0"), (-1.0, "-1.0"),
        (0.5, "0.5"), (1.5, "1.5"), (100.0, "100.0"),
        # A float always shows a decimal point (PF_FLAG_ALWAYS_DECIMAL).
        (1e15, "1000000000000000.0"),
        # ... until the exponent reaches 16, where 'g' switches form.
        (1e16, "1e+16"),
        # -4 is the other end of the fixed range.
        (1e-4, "0.0001"), (1e-5, "1e-05"),
        # Three exponent digits when they are needed, two otherwise.
        (1e100, "1e+100"), (1e-100, "1e-100"),
        # Sixteen digits, not the shortest form: this is the whole point.
        (8.888e32, "8.888000000000001e+32"),
        # Seventeen when sixteen do not identify the double.
        (2.0740190835122216e16, "2.0740190835122216e+16"),
    )
    for value, want in cases:
        got = F.float_repr(value)
        check(got == want, "%r prints as %s (got %s)" % (value, want, got))


def test_big():
    """The multiprecision integer's own arithmetic."""
    b = F.Big(0)
    check(b.is_zero() and b.decimal() == "0", "zero")
    b = F.Big(1)
    b.mul_pow(10, 40)
    check(b.decimal() == "1" + "0" * 40, "10**40")
    b = F.Big(1)
    b.mul_pow(5, 100)
    check(b.decimal() == "%d" % (5 ** 100), "5**100")
    b = F.Big(1)
    b.mul_pow(2, 300)
    check(b.decimal() == "%d" % (2 ** 300), "2**300")
    b = F.Big(123456789)
    rem = b.divmod_small(10000)
    check(rem == 6789 and b.decimal() == "12345", "divide with a remainder")
    a = F.Big(1)
    a.mul_pow(10, 50)
    c = F.Big(1)
    c.mul_pow(10, 50)
    check(a.cmp(c) == 0, "equal compares equal")
    c.mul_small(2)
    check(a.cmp(c) < 0 and c.cmp(a) > 0, "unequal compares both ways")
    # Enough limbs to need many, as a subnormal's expansion does.
    b = F.Big(1)
    b.mul_pow(5, 1074)
    check(len(b.decimal()) == 751, "5**1074 has 751 digits")


def test_rounding():
    """Half-to-even on an exact tie, up otherwise."""
    cases = (("1234567890123456500", 16, "1234567890123456", 0),
             ("1234567890123457500", 16, "1234567890123458", 0),
             ("1234567890123456501", 16, "1234567890123457", 0),
             ("9999999999999999999", 16, "1000000000000000", 1),
             ("12345", 16, "1234500000000000", 0))
    for digits, nd, want, want_carry in cases:
        got, carry = F._round_digits(digits, nd)
        check(got == want and carry == want_carry,
              "%s to %d digits is %s/%d (got %s/%d)"
              % (digits, nd, want, want_carry, got, carry))


def digest():
    """One digest over the whole sample, to compare between runtimes."""
    acc = 0
    n = 0
    for v in sample():
        n += 1
        for ch in F.float_repr(v):
            acc = (acc * 31 + ord(ch)) & 0xFFFFFFFF
    return n, acc


def denotes(text, value):
    """Does *text*, read back correctly, denote exactly *value*?

    Decided by exact arithmetic, not by ``float(text)``: the whole point is
    not to take the runtime's parser as the authority, and MicroPython's is
    not correctly rounded, so asking it would make the answer depend on where
    the test runs.
    """
    if text[0] == "-":
        if not ("%g" % value)[0] == "-":
            return False
        text = text[1:]
        value = -value
    elif ("%g" % value)[0] == "-":
        return False
    mant, _sep, exp_text = text.partition("e")
    exp = int(exp_text) if exp_text else 0
    whole, _dot, frac = mant.partition(".")
    digits = whole + frac
    exp -= len(frac)
    d = int(digits)
    while d != 0 and d % 10 == 0:
        d //= 10
        exp += 1
    if d == 0:
        return value == 0.0
    m, e = F._decompose(value)
    return F._round_trips(d, exp, m, e)


def classify_against_oracle(path):
    """Compare with the pinned runtime's own repr, value by value.

    Three outcomes, and only the last is a failure:

    * identical text;
    * different text that still denotes the same double -- the deviation
      DEVIATIONS.md D2 describes and bounds;
    * text of ours that does not denote the value it came from, which would
      be a bug.

    The oracle's own text landing in neither of the first two is reported
    separately rather than counted against us: `mpy-cross` verifies its
    spelling by re-reading it with MicroPython's parser, which is inexact in
    the same direction as its formatter, so the two agree with each other
    without the text being a faithful decimal for the double.
    """
    f = open(path)
    same = 0
    spelling = 0
    oracle_lossy = 0
    wrong = 0
    for v in sample():
        ref = f.readline().rstrip("\n")
        if not ref:
            print("oracle file is shorter than the sample")
            return
        mine = F.float_repr(v)
        if v != v or v == F._INF or v == -F._INF:
            check(mine == ref, "%s matches the oracle" % mine)
            same += 1
            continue
        if not denotes(mine, v):
            wrong += 1
            if wrong <= 10:
                print("WRONG: %s does not denote %s" % (mine, repr(v)))
            continue
        if mine == ref:
            same += 1
        elif denotes(ref, v):
            spelling += 1
        else:
            oracle_lossy += 1
            if oracle_lossy <= 10:
                print("oracle text does not denote the value: ours %s, "
                      "oracle %s" % (mine, ref))
    f.close()
    print("oracle     : %d identical, %d spelled differently (same value), "
          "%d oracle spellings that do not denote their value, %d wrong"
          % (same, spelling, oracle_lossy, wrong))
    check(wrong == 0, "every spelling denotes the value it came from")


def main(argv):
    oracle = None
    it = iter(argv)
    for a in it:
        if a == "--oracle":
            oracle = next(it)
    test_big()
    test_rounding()
    test_shapes()
    test_round_trips()
    if oracle:
        classify_against_oracle(oracle)
    n, acc = digest()
    print("digest     : %08x over %d values" % (acc, n))
    print("")
    if _fails:
        print("summary: failed=%d" % len(_fails))
        return 1
    print("summary: all float-spelling checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
