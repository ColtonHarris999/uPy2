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

# Source: mpy-cross/build
COUNT = 416

BLOB = (
    b'\x03%#o\x03%#x\n<dictcomp>\t<gen'
    b'expr>\x08<lambda>\n<listcomp'
    b'>\t<setcomp>\x07<stdin>\x08<str'
    b'ing>\rInterpolation\x04None\x12'
    b'StopAsyncIteration\x08Templ'
    b'ate\x0cUnicodeError\x0eViperTy'
    b'peError\x07__add__\n__aenter'
    b'__\t__aexit__\t__aiter__\t_'
    b'_anext__\t__bases__\x08__boo'
    b'l__\x0f__build_class__\x0b__co'
    b'mplex__\x0c__contains__\x08__d'
    b'ict__\x06__eq__\t__float__\x06_'
    b'_ge__\x06__gt__\x08__iadd__\n__'
    b'import__\x08__isub__\x06__le__'
    b'\x06__lt__\x06__ne__\x08__path__\x0e'
    b'__repl_print__\x0c__reverse'
    b'd__\x07__sub__\x0c__template__'
    b'\r__traceback__\x02a0\x02a1\x03a10'
    b'\x03a11\x03a12\x03a13\x03a14\x03a15\x02a2\x02'
    b'a3\x02a4\x02a5\x02a6\x02a7\x02a8\x02a9\x04abs'
    b'_\x04acos\x03add\x05add_n\x04addi\x06ad'
    b'di_n\x05addmi\x05addx2\x05addx4\x05a'
    b'ddx8\x05align\x04and_\x04andi\x05asc'
    b'ii\x04asin\x08asm_rv32\tasm_thu'
    b'mb\nasm_xtensa\x03asr\x04atan\x05a'
    b'tan2\x05auipc\x01b\x04ball\x04bany\x03b'
    b'bc\x04bbci\x03bbs\x04bbsi\x03bcc\x04bcc'
    b'z\x03beq\x04beqz\x06beqz_n\x03bge\x04bg'
    b'eu\x04bgez\x03big\x03bin\nbit_bran'
    b'ch\x02bl\x03blt\x04bltu\x04bltz\x05bnal'
    b'l\x03bne\x04bnez\x06bnez_n\x05bnone\x0c'
    b'bound_method\x02bx\tbyteorde'
    b'r\x05c_add\x06c_addi\nc_addi4sp'
    b'n\x05c_and\x06c_andi\x06c_beqz\x06c_'
    b'bnez\x08c_ebreak\x03c_j\x05c_jal\x06'
    b'c_jalr\x04c_jr\x04c_li\x05c_lui\x04c'
    b'_lw\x06c_lwsp\x04c_mv\x05c_nop\x04c_'
    b'or\x06c_slli\x06c_srai\x06c_srli\x05'
    b'c_sub\x04c_sw\x06c_swsp\x05c_xor\x08'
    b'calcsize\x05call0\x06callx0\x04ce'
    b'il\x07closure\x03clz\tcm_mva01s'
    b'\tcm_mvsa01\x06cm_pop\tcm_pop'
    b'ret\ncm_popretz\x07cm_push\x03c'
    b'mp\x07complex\nconversion\x08co'
    b'pysign\x03cos\x05cpsid\x05cpsie\x05c'
    b'srrc\x06csrrci\x05csrrs\x06csrrsi'
    b'\x05csrrw\x06csrrwi\x04data\x06decod'
    b'e\x07default\x07degrees\x07delatt'
    b'r\x07deleter\tdict_view\ndiff'
    b'erence\x11difference_update'
    b'\x07discard\x03div\x04divu\x03doc\x01e\x06'
    b'ebreak\x05ecall\x06encode\tenum'
    b'erate\x05errno\x03exp\nexpressi'
    b'on\x04fabs\x06filter\x05float\x05flo'
    b'or\x04fmod\x0bformat_spec\x05frex'
    b'p\x08fromkeys\x08function\tgene'
    b'rator\x06getter\x02gp\theap_loc'
    b'k\x0bheap_unlock\x03hex\x04imag\x03i'
    b'nf\x07integer\x0einterpolation'
    b's\x0cintersection\x13intersect'
    b'ion_update\nisdisjoint\x08is'
    b'finite\x05isinf\x05isnan\x08issub'
    b'set\nissuperset\x08iterable\x08'
    b'iterator\x01j\x03jal\x04jalr\x02jx\x05l'
    b'16si\x05l16ui\x04l32i\x06l32i_n\x04l'
    b'32r\x04l8ui\x02la\x05label\x02lb\x03lbu'
    b'\x05ldexp\x03ldr\x04ldrb\x05ldrex\x04ld'
    b'rh\x06length\x02lh\x03lhu\x02li\x08loca'
    b'tion\x03log\x03lsl\x03lsr\x03lui\x02lw\x04'
    b'math\x03max maximum recursi'
    b'on depth exceeded\x03min\x04mo'
    b'df\x06module\x03mov\x05mov_n\x04movi'
    b'\x06movi_n\x04movt\x04movw\x05movwt\x03'
    b'mrs\x03mul\x04mulh\x06mulhsu\x05mulh'
    b'u\x04mull\x02mv\x03nan\x06native\x03neg'
    b'\x03nop\x05nop_n\x03nsa\x04nsau\x03oct\x06'
    b'offset\topt_level\x03or_\x03ori'
    b'\x04pack\tpack_into\npend_thr'
    b'ow\x02pi\x08property\x03ptr\x05ptr16'
    b'\x05ptr32\x04ptr8\x04push\x02ra\x07radi'
    b'ans\x04rbit\x04real\x08register\x03r'
    b'em\x04remu\x03ret\x05ret_n\x08revers'
    b'ed\x02s0\x02s1\x03s10\x03s11\x04s16i\x02s2'
    b'\x02s3\x04s32i\x06s32i_n\x02s4\x02s5\x02s6'
    b'\x02s7\x02s8\x03s8i\x02s9\x02sb\x04sdiv\x06se'
    b'tter\x02sh\x06sh1add\x06sh2add\x06sh'
    b'3add\x06signed\x03sin\x05slice\x03sl'
    b'l\x04slli\x03slt\x04slti\x05sltiu\x04sl'
    b'tu\x02sp\x04sqrt\x03sra\x04srai\x03src\x03'
    b'srl\x04srli\x05ssa8b\x05ssa8l\x04ssa'
    b'i\x03ssl\x03ssr\x04strb\x05strex\x04str'
    b'h\x06string\x12string.template'
    b'lib\x07strings\x06struct\x03sub\x05s'
    b'ubx2\x05subx4\x05subx8\x02sw\x14symm'
    b'etric_difference\x1bsymmetr'
    b'ic_difference_update\x02t0\x02'
    b't1\x02t2\x02t3\x02t4\x02t5\x02t6\x03tan\x06ta'
    b'rget\x03tau\x0btemplatelib\x02tp\x05'
    b'trunc\x04udiv\x04uint\x05union\x06un'
    b'pack\x0bunpack_from\x04utf8\x04vc'
    b'mp\x0cvcvt_f32_s32\x0cvcvt_s32'
    b'_f32\x05viper\x04vldr\x04vmov\x04vmr'
    b's\x04vneg\x05vsqrt\x04vstr\x03wfi\x02x0'
    b'\x02x1\x03x10\x03x11\x03x12\x03x13\x03x14\x03'
    b'x15\x03x16\x03x17\x03x18\x03x19\x02x2\x03x'
    b'20\x03x21\x03x22\x03x23\x03x24\x03x25\x03x'
    b'26\x03x27\x03x28\x03x29\x02x3\x03x30\x03x3'
    b'1\x02x4\x02x5\x02x6\x02x7\x02x8\x02x9\x03xor\x04'
    b'xori\x04zero\x05{:#b}'
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
