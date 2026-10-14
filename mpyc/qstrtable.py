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
"""

from . import qstrs
from .mpy_format import StaticQstr


class QstrTable:
    """Collects the qstrs a module uses, in first-use order."""

    def __init__(self):
        self._texts = []
        self._index = {}

    def __len__(self):
        return len(self._texts)

    def index(self, text):
        """The table index for *text*, appending it on first use."""
        i = self._index.get(text)
        if i is not None:
            return i
        i = len(self._texts)
        self._texts.append(text)
        self._index[text] = i
        return i

    def text(self, index):
        return self._texts[index]

    def texts(self):
        return list(self._texts)

    def entries(self):
        """The table in the form ``mpy_format.encode_qstr`` expects.

        A static qstr becomes ``StaticQstr(number)``; anything else becomes
        its UTF-8 bytes.
        """
        out = []
        for text in self._texts:
            n = qstrs.static_index(text)
            if n is None:
                out.append(text.encode("utf-8"))
            else:
                out.append(StaticQstr(n))
        return out


def entry_text(entry):
    """The text an ``.mpy`` qstr table entry stands for.

    Accepts what ``mpy_format.decode_qstr`` produces, so a decoded file can be
    compared against a table built here.
    """
    if isinstance(entry, StaticQstr):
        return qstrs.static_text(entry.index)
    return entry.decode("utf-8")
