"""The non-static part of one build's qstr pool -- GENERATED.

Regenerate with ``tools/gen_qstr_pool.py <tree> [--build DIR]``.

A string literal longer than 10 bytes becomes a qstr leaf only if an
equal string is already interned, and a constant object otherwise --
different bytecode.  So this pool changes what gets compiled, and it
is specific to the build it came from: mpy-cross's pool is not a
firmware's.  Membership is all that is needed; non-static qstrs are
written to an .mpy as text, so their numbers never escape.

The entries are one sorted, length-prefixed blob, searched by
bisection.  A set of separate string objects costs roughly five times
as much on the target, where every object carries a header.
"""

from array import array

# Source: ports/unix/build-standard
COUNT = 607

BLOB = (
    b'\x03%#o\x03%#x\x04... \x07.frozen\n<d'
    b'ictcomp>\t<genexpr>\x08<lamb'
    b'da>\n<listcomp>\t<setcomp>'
    b'\x07<stdin>\x08<string>\x04>>> \x07A'
    b'F_INET\x08AF_INET6\x07AF_UNIX\x05'
    b'ARRAY\x04AUTO\x08B1000000\x07B115'
    b'200\x08B1152000\x07B230400\x07B46'
    b'0800\x07B500000\x06B57600\x07B576'
    b'000\x07B921600\x05B9600\x07BFINT1'
    b'6\x07BFINT32\x06BFINT8\x08BFUINT1'
    b'6\x08BFUINT32\x07BFUINT8\x06BF_LE'
    b'N\x06BF_POS\nBIG_ENDIAN\x07Byte'
    b'sIO\tCERT_NONE\rCERT_OPTIO'
    b'NAL\rCERT_REQUIRED\x0eCancel'
    b'ledError\x04DESC\tDeflateIO\x06'
    b'EACCES\nEADDRINUSE\x06EAGAIN'
    b'\x08EALREADY\x05EBADF\x0cECONNABO'
    b'RTED\x0cECONNREFUSED\nECONNR'
    b'ESET\x06EEXIST\x0cEHOSTUNREACH'
    b'\x0bEINPROGRESS\x06EINVAL\x03EIO\x06'
    b'EISDIR\x07ENOBUFS\x06ENODEV\x06EN'
    b'OENT\x06ENOMEM\x08ENOTCONN\nEOP'
    b'NOTSUPP\x05EPERM\tETIMEDOUT\x07'
    b'FLOAT32\x07FLOAT64\x06FileIO\x0bF'
    b'rameBuffer\x0cFrameBuffer1\x08'
    b'GS2_HMSB\x08GS4_HMSB\x03GS8\x04GZ'
    b'IP\x04INCL\x03INT\x05INT16\x05INT32\x05'
    b'INT64\x04INT8\x06IOBase\x11IP_ADD'
    b'_MEMBERSHIP\x12IP_DROP_MEMB'
    b'ERSHIP\rLITTLE_ENDIAN\x04LON'
    b'G\x08LONGLONG\x08LockType\x0fMBED'
    b'TLS_VERSION\tMONO_HLSB\tMO'
    b'NO_HMSB\tMONO_VLSB\rMSG_DO'
    b'NTROUTE\x0cMSG_DONTWAIT\x08MSG'
    b'_PEEK\x05MVLSB\x06NATIVE\x04None\x0e'
    b'NotImplemented\x0bOrderedDi'
    b'ct\x07POLLERR\x07POLLHUP\x06POLLI'
    b'N\x07POLLOUT\x14PROTOCOL_DTLS_'
    b'CLIENT\x14PROTOCOL_DTLS_SER'
    b'VER\x13PROTOCOL_TLS_CLIENT\x13'
    b'PROTOCOL_TLS_SERVER\x03PTR\x07'
    b'PinBase\x03RAW\x06RGB565\x06RingI'
    b'O\x05SHORT\nSOCK_DGRAM\x08SOCK_'
    b'RAW\x0bSOCK_STREAM\nSOL_SOCK'
    b'ET\x0cSO_BROADCAST\x08SO_ERROR'
    b'\x0cSO_KEEPALIVE\tSO_LINGER\x0c'
    b'SO_REUSEADDR\nSSLContext\t'
    b'SSLSocket\x06Signal\x12StopAsy'
    b'ncIteration\x08StringIO\x07TCS'
    b'ANOW\x04Task\tTaskQueue\rText'
    b'IOWrapper\x04UINT\x06UINT16\x06UI'
    b'NT32\x06UINT64\x05UINT8\x05ULONG\t'
    b'ULONGLONG\x06USHORT\x0cUnicode'
    b'Error\x04VOID\x06VfsFat\x07VfsLfs'
    b'1\x07VfsLfs2\x08VfsPosix\x06VfsRo'
    b'm\x0eViperTypeError\x04ZLIB\x07__'
    b'abs__\x07__add__\n__aenter__'
    b'\t__aexit__\t__aiter__\x07__a'
    b'll__\x07__and__\t__anext__\t_'
    b'_bases__\x08__bool__\x0f__buil'
    b'd_class__\x0b__complex__\x0c__'
    b'contains__\x07__del__\x0b__del'
    b'attr__\n__delete__\x08__dict'
    b'__\n__divmod__\x06__eq__\x08__f'
    b'ile__\t__float__\x0c__floord'
    b'iv__\x06__ge__\x07__get__\x0b__gl'
    b'obals__\x06__gt__\x08__iadd__\n'
    b'__import__\n__invert__\x08__'
    b'isub__\x06__le__\n__lshift__'
    b'\x06__lt__\n__matmul__\x07__mod'
    b'__\x07__mul__\x06__ne__\x07__neg_'
    b'_\x06__or__\x08__path__\x07__pos_'
    b'_\x07__pow__\x08__radd__\x08__ran'
    b'd__\x0e__repl_print__\x0c__rev'
    b'ersed__\r__rfloordiv__\x0b__'
    b'rlshift__\x0b__rmatmul__\x08__'
    b'rmod__\x08__rmul__\x07__ror__\x08'
    b'__rpow__\x0b__rrshift__\n__r'
    b'shift__\x08__rsub__\x0c__rtrue'
    b'div__\x08__rxor__\x07__set__\x0c_'
    b'_set_name__\x0b__setattr__\x07'
    b'__sub__\r__traceback__\x0b__'
    b'truediv__\x07__xor__\x08_async'
    b'io\x06_build\x08_machine\x04_mpy\x0b'
    b'_task_queue\x07_thread\na2b_'
    b'base64\x07abs_tol\x06accept\x04ac'
    b'os\x05acosh\x07acquire\x03add\x04add'
    b'r\taddressof\x03aes\rallocate'
    b'_lock\nappendleft\x04argv\x05ar'
    b'ray\x0cas_bytearray\x05ascii\x04a'
    b'sin\x05asinh\x04atan\x05atan2\x05ata'
    b'nh\x06atexit\nb2a_base64\x03big'
    b'\x03bin\x08binascii\x04bind\x04blit\x0c'
    b'bound_method\x05btree\x06buffe'
    b'r\tbuffering\x0cbytearray_at'
    b'\tbyteorder\x08bytes_at\tcach'
    b'esize\x08calcsize\x08callback\x06'
    b'cancel\x04ceil\x06center\x04cfun\x05'
    b'chdir\x06choice\x06cipher\tclie'
    b'nt_id\x05clock\x07closure\x05cmat'
    b'h\x04code\x07collect\x0bcollectio'
    b'ns\x07compile\x07complex\x07conne'
    b'ct\x08copysign\x04coro\x03cos\x04cos'
    b'h\x05crc32\tcryptolib\x08cur_ta'
    b'sk\x04data\x06decode\x07decrypt\x07d'
    b'efault\x07deflate\x07degrees\x07d'
    b'elattr\x07deleter\x05deque\tdic'
    b't_view\ndifference\x11differ'
    b'ence_update\x06digest\x07disab'
    b'le\x07discard\x17do_handshake_'
    b'on_connect\x03doc\x04done\x04dump'
    b'\x05dumps\x01e\x07ellipse\x06enable\x06'
    b'encode\x08encoding\x07encrypt\t'
    b'enumerate\x03erf\x04erfc\x05errno'
    b'\terrorcode\x08exc_info\x08exec'
    b'file\nexecutable\x04exit\x03exp'
    b'\x05expm1\x04fabs\tfactorial\x03ff'
    b'i\x0bfficallback\x07ffifunc\x06ff'
    b'imod\x06ffivar\x04file\x06fileno\x04'
    b'fill\tfill_rect\x06filter\x05fl'
    b'ags\x05float\x05floor\x05flush\x04fm'
    b'od\x08framebuf\x05frexp\x07fromhe'
    b'x\x08fromkeys\tfrozenset\x04fun'
    b'c\x08function\x05gamma\x02gc\tgene'
    b'rator\x0bget_ciphers\tget_id'
    b'ent\x0bgetaddrinfo\x06getcwd\x06g'
    b'etenv\x0bgetrandbits\x06getter'
    b'\x08getvalue\x06gmtime\x05group\x07h'
    b'ashlib\theap_lock\x0bheap_un'
    b'lock\x07heapify\x07heappop\x08hea'
    b'ppush\x05heapq\x04help\x03hex\x07hex'
    b'lify\x05hline\x04idle\x06ignore\x08i'
    b'listdir\x04imag\x0eimplementat'
    b'ion\x07indices\tinet_ntop\tin'
    b'et_pton\x03inf\x05input\x0cinters'
    b'ection\x13intersection_upda'
    b'te\x06invert\x02io\x05ioctl\x05ipoll'
    b'\x07isclose\nisdisjoint\tisen'
    b'abled\x08isfinite\x05isinf\x05isn'
    b'an\x08issubset\nissuperset\x08i'
    b'temsize\x08iterable\x08iterato'
    b'r\x04json\x08kbd_intr\x08keepends'
    b'\x05ldexp\x06length\x06lgamma\x08lib'
    b'c_ver\x04line\x07listdir\x06liste'
    b'n\x04load\x0fload_cert_chain\x15l'
    b'oad_verify_locations\x05loa'
    b'ds\tlocaltime\x04lock\x06locked'
    b'\x03log\x05log10\x04log2\tlookahea'
    b'd\x07machine\x08makefile\x05match'
    b'\x04math\x03max maximum recurs'
    b'ion depth exceeded\x07maxsi'
    b'ze\x03md5\x03mem\x05mem16\x05mem32\x04m'
    b'em8\tmem_alloc\x0bmem_curren'
    b't\x08mem_free\x08mem_info\x08mem_'
    b'peak\tmem_total\nmemoryvie'
    b'w\x03min\nminkeypage\x05mkdir\x04m'
    b'kfs\x06mktime\x04mode\x04modf\x06mod'
    b'ify\x06module\x07modules\x05mount'
    b'\x05mtime\x04name\nnamedtuple\x03n'
    b'an\x06native\x07newline\x03oct\x03of'
    b'f\x02on\topt_level\x02os\x04pack\tp'
    b'ack_into\x08pagesize\tpartit'
    b'ion\x04path\x04peek\npend_throw'
    b'\x06ph_key\x05phase\x02pi\x05pixel\x08p'
    b'latform\x05polar\x04poll\x04poly\x07'
    b'popleft\x0fprint_exception\x08'
    b'progsize\x08property\x03ps1\x03ps'
    b'2\x0cpsk_identity\x07psk_key\x03p'
    b'tr\x05ptr16\x05ptr32\x04ptr8\x04push'
    b'\x03put\x06putenv\x0fpython_compi'
    b'ler\tqstr_info\x01r\x07radians\x07'
    b'randint\x06random\trandrange'
    b'\x02rb\x02re\nreadblocks\treadli'
    b'nes\x08readonly\x08readsize\x04re'
    b'al\x04rect\x04recv\trecv_into\x08r'
    b'ecvfrom\x08register\x07rel_tol'
    b'\x07release\x06rename\x08reversed'
    b'\x05rmdir\nrpartition\x08schedu'
    b'le\x06scroll\x06search\x04seed\x04se'
    b'ek\x06select\x07sendall\x06sendto'
    b'\nseparators\x03seq\x0fserver_h'
    b'ostname\x0fserver_psk_keys\x0b'
    b'server_side\x0bset_ciphers\x0b'
    b'setblocking\x06setraw\nsetso'
    b'ckopt\x06setter\nsettimeout\x04'
    b'sha1\x06sha256\x06signed\x03sin\x06s'
    b'ingle\x04sinh\x06sizeof\x05sleep\x08'
    b'sleep_ms\x08sleep_us\x05slice\x08'
    b'sockaddr\x06socket\nsoft_res'
    b'et\nsplitlines\x04sqrt\nstack'
    b'_size\tstack_use\x10start_ne'
    b'w_thread\x04stat\x05state\x07stat'
    b'vfs\x06stderr\x05stdin\x06stdout\x06'
    b'struct\x03sub\x14symmetric_dif'
    b'ference\x1bsymmetric_differ'
    b'ence_update\x04sync\x03sys\x06sys'
    b'tem\x03tan\x04tanh\x03tau\ttcgetat'
    b'tr\ttcsetattr\x04tell\x07termio'
    b's\x04text\tthreshold\tticks_a'
    b'dd\tticks_cpu\nticks_diff\x08'
    b'ticks_ms\x08ticks_us\x04time\x07t'
    b'ime_ns\rtime_pulse_us\x03tls'
    b'\x05trunc\x07uctypes\x04uint\x06umou'
    b'nt\tunhexlify\x07uniform\x05uni'
    b'on\x06unlink\x06unpack\x0bunpack_'
    b'from\nunregister\x06unsafe\x08u'
    b'nsetenv\x07urandom\x04usys\x04utf'
    b'8\x03var\x0fverify_callback\x0bve'
    b'rify_mode\x07version\x0cversio'
    b'n_info\x03vfs\x05viper\x05vline\tw'
    b'ebsocket\x0bwrap_socket\x0bwri'
    b'teblocks\x05{:#b}'
)

# Offset of each entry's length byte, built once at import.
_OFFSETS = array('I')
_pos = 0
while _pos < len(BLOB):
    _OFFSETS.append(_pos)
    _pos += 1 + BLOB[_pos]


def entry(i):
    """Entry *i*, as bytes."""
    off = _OFFSETS[i]
    return BLOB[off + 1:off + 1 + BLOB[off]]


def contains(raw):
    """Is *raw* (bytes) in this build's pool?"""
    lo = 0
    hi = len(_OFFSETS) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        cur = entry(mid)
        if cur == raw:
            return True
        if cur < raw:
            lo = mid + 1
        else:
            hi = mid - 1
    return False


class Pool:
    """``raw in pool`` for the interning pool to consult."""

    def __contains__(self, raw):
        return contains(raw)

    def __len__(self):
        return COUNT


POOL = Pool()
