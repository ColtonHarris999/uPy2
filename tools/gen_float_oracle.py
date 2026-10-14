"""Write the pinned runtime's own ``repr`` for the float sample.

``tests/test_floatfmt.py --oracle FILE`` compares what the compiler spells
against what MicroPython itself spells, value by value.  The file is not
checked in: it has to come from the pinned runtime, and it is tied to the
order of that test's ``sample()``, so it is generated rather than stored.

Run it *with the pinned MicroPython*, not with CPython -- CPython's ``repr``
is the shortest round-tripping form, which is not what MicroPython prints:

    <pinned micropython> tools/gen_float_oracle.py > /tmp/o.txt
    python3 tests/test_floatfmt.py --oracle /tmp/o.txt
    micropython tests/test_floatfmt.py --oracle /tmp/o.txt
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tests")
sys.path.insert(0, "..")

from test_floatfmt import sample  # noqa: E402

for _v in sample():
    print(repr(_v))
