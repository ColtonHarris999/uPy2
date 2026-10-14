"""The per-module qstr table that goes into an ``.mpy`` file.

An ``.mpy`` carries its own table of qstrs (``mp_raw_code_save`` writes
``n_qstr`` then each entry); bytecode operands index *that* table, not the
interpreter's global qstr numbering.  Each entry is written one of two ways by
``save_qstr``:

* a qstr in the static pool -> its fixed global number, as ``index << 1 | 1``
* anything else -> ``len << 1`` then the text and a NUL

Both kinds share the one table and are interleaved in first-use order -- the
oracle's own output shows ``'<module>'`` (static) and ``'<listcomp>'`` (not)
sitting side by side -- so being static changes only how an entry is
*written*, never whether it gets a slot.

The table is keyed by qstr *number*, as ``mp_emit_common_use_qstr`` is: the
interning pool makes numbers and texts one-to-one, but numbers are what the
parse tree and the scopes carry, so keying on them avoids converting back and
forth on every single operand.
"""

from . import qstrs
from .mpy_format import StaticQstr


class QstrTable:
    """Collects the qstrs a module uses, in first-use order."""

    def __init__(self, pool):
        # The parse's interning pool, used only to spell an entry out.
        self.pool = pool
        self._numbers = []
        self._index = {}

    def __len__(self):
        return len(self._numbers)

    def index(self, qst):
        """The table index for qstr number *qst*, appending it on first use."""
        i = self._index.get(qst)
        if i is not None:
            return i
        i = len(self._numbers)
        self._numbers.append(qst)
        self._index[qst] = i
        return i

    def number(self, index):
        """The qstr number at *index*."""
        return self._numbers[index]

    def numbers(self):
        return list(self._numbers)

    def text(self, index):
        """The bytes of the qstr at *index*."""
        return self.pool.text(self._numbers[index])

    def entries(self):
        """The table in the form ``mpy_format.encode_qstr`` expects.

        A static qstr becomes ``StaticQstr(number)``; anything else becomes
        its bytes.
        """
        out = []
        for qst in self._numbers:
            if qstrs.is_static(qst):
                out.append(StaticQstr(qst))
            else:
                out.append(self.pool.text(qst))
        return out


def entry_text(entry):
    """The text an ``.mpy`` qstr table entry stands for, as bytes.

    Accepts what ``mpy_format.decode_qstr`` produces, so a decoded file can be
    compared against a table built here.
    """
    if isinstance(entry, StaticQstr):
        return qstrs.static_text(entry.index).encode("utf-8")
    return entry
