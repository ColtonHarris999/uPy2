"""MicroPython's static qstr pool -- GENERATED, do not edit.

Regenerate with ``tools/gen_qstrs.py <micropython-source-tree>``.

These 166 strings have fixed qstr numbers in every MicroPython build, so
an ``.mpy`` file may refer to them by number rather than spelling them
out.  ``py/makeqstrdata.py`` emits ``MP_QSTRnull`` and then its literal
``static_qstr_list``; everything after this pool is sorted per build and
has no stable number.

Index 0 is ``MP_QSTRnull``, a sentinel, and its text is also the empty
string -- the real empty string is index 1 (``MP_QSTR_``).  Interning
"" must therefore yield 1, never 0, which is why the lookup table below
skips index 0.
"""

# Source: mpy
# Cross-checked against: mpy-cross/build/genhdr/qstrdefs.generated.h, ports/unix/build-standard/genhdr/qstrdefs.generated.h

QSTR_NULL = 0
QSTR_EMPTY = 1
QSTR_LAST_STATIC = 165
STATIC_POOL_SIZE = 166

# index -> text; index is the qstr number
STATIC_QSTRS = (
    '',  # MP_QSTRnull (sentinel)
    '',
    '__dir__',
    '\n',
    ' ',
    '*',
    '/',
    '<module>',
    '_',
    '__call__',
    '__class__',
    '__delitem__',
    '__enter__',
    '__exit__',
    '__getattr__',
    '__getitem__',
    '__hash__',
    '__init__',
    '__int__',
    '__iter__',
    '__len__',
    '__main__',
    '__module__',
    '__name__',
    '__new__',
    '__next__',
    '__qualname__',
    '__repr__',
    '__setitem__',
    '__str__',
    'ArithmeticError',
    'AssertionError',
    'AttributeError',
    'BaseException',
    'EOFError',
    'Ellipsis',
    'Exception',
    'GeneratorExit',
    'ImportError',
    'IndentationError',
    'IndexError',
    'KeyError',
    'KeyboardInterrupt',
    'LookupError',
    'MemoryError',
    'NameError',
    'NoneType',
    'NotImplementedError',
    'OSError',
    'OverflowError',
    'RuntimeError',
    'StopIteration',
    'SyntaxError',
    'SystemExit',
    'TypeError',
    'ValueError',
    'ZeroDivisionError',
    'abs',
    'all',
    'any',
    'append',
    'args',
    'bool',
    'builtins',
    'bytearray',
    'bytecode',
    'bytes',
    'callable',
    'chr',
    'classmethod',
    'clear',
    'close',
    'const',
    'copy',
    'count',
    'dict',
    'dir',
    'divmod',
    'end',
    'endswith',
    'eval',
    'exec',
    'extend',
    'find',
    'format',
    'from_bytes',
    'get',
    'getattr',
    'globals',
    'hasattr',
    'hash',
    'id',
    'index',
    'insert',
    'int',
    'isalpha',
    'isdigit',
    'isinstance',
    'islower',
    'isspace',
    'issubclass',
    'isupper',
    'items',
    'iter',
    'join',
    'key',
    'keys',
    'len',
    'list',
    'little',
    'locals',
    'lower',
    'lstrip',
    'main',
    'map',
    'micropython',
    'next',
    'object',
    'open',
    'ord',
    'pop',
    'popitem',
    'pow',
    'print',
    'range',
    'read',
    'readinto',
    'readline',
    'remove',
    'replace',
    'repr',
    'reverse',
    'rfind',
    'rindex',
    'round',
    'rsplit',
    'rstrip',
    'self',
    'send',
    'sep',
    'set',
    'setattr',
    'setdefault',
    'sort',
    'sorted',
    'split',
    'start',
    'startswith',
    'staticmethod',
    'step',
    'stop',
    'str',
    'strip',
    'sum',
    'super',
    'throw',
    'to_bytes',
    'tuple',
    'type',
    'update',
    'upper',
    'utf-8',
    'value',
    'values',
    'write',
    'zip',
)

# text -> qstr number, excluding the index 0 sentinel
_INDEX = {}
for _i in range(1, STATIC_POOL_SIZE):
    _INDEX[STATIC_QSTRS[_i]] = _i


def static_index(text):
    """The fixed qstr number for *text*, or None if it is not static."""
    return _INDEX.get(text)


def is_static(qstr_number):
    return 0 <= qstr_number <= QSTR_LAST_STATIC


def static_text(qstr_number):
    """The text of a static qstr number."""
    return STATIC_QSTRS[qstr_number]
