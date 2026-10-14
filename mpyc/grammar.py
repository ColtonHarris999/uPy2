"""MicroPython's grammar tables -- GENERATED, do not edit.

Regenerate with ``tools/gen_grammar.py <micropython-source-tree>``.

A struct parse node's kind is its rule index, so this numbering is part
of the format the compiler reads.  ``py/parse.c`` orders the rules as
every DEF_RULE in file order, then RULE_const_object, then every
DEF_RULE_NC; that split is reproduced here.
"""

from array import array

# Generated from mpy
# Configuration:
#   MICROPY_COMP_FSTRING_LITERAL = 1
#   MICROPY_PY_ASSIGN_EXPR = 1
#   MICROPY_PY_ASYNC_AWAIT = 1
#   MICROPY_PY_BUILTINS_SET = 1
#   MICROPY_PY_BUILTINS_SLICE = 1
#   MICROPY_PY_FSTRINGS = 1
#   MICROPY_PY_TSTRINGS = 1

# -- bit layout (py/parse.c) ---------------------------------------
RULE_ACT_ARG_MASK = 0x0F
RULE_ACT_KIND_MASK = 0x30
RULE_ACT_ALLOW_IDENT = 0x40
RULE_ACT_ADD_BLANK = 0x80
RULE_ACT_OR = 0x10
RULE_ACT_AND = 0x20
RULE_ACT_LIST = 0x30

RULE_ARG_KIND_MASK = 0xF000
RULE_ARG_ARG_MASK = 0x0FFF
RULE_ARG_TOK = 0x1000
RULE_ARG_RULE = 0x2000
RULE_ARG_OPT_RULE = 0x3000

# -- rule numbering ------------------------------------------------
RULE_file_input = 0
RULE_file_input_2 = 1
RULE_decorated = 2
RULE_funcdef = 3
RULE_simple_stmt_2 = 4
RULE_expr_stmt = 5
RULE_testlist_star_expr = 6
RULE_del_stmt = 7
RULE_pass_stmt = 8
RULE_break_stmt = 9
RULE_continue_stmt = 10
RULE_return_stmt = 11
RULE_yield_stmt = 12
RULE_raise_stmt = 13
RULE_import_name = 14
RULE_import_from = 15
RULE_global_stmt = 16
RULE_nonlocal_stmt = 17
RULE_assert_stmt = 18
RULE_async_stmt = 19
RULE_if_stmt = 20
RULE_while_stmt = 21
RULE_for_stmt = 22
RULE_try_stmt = 23
RULE_with_stmt = 24
RULE_suite_block_stmts = 25
RULE_namedexpr_test = 26
RULE_test_if_expr = 27
RULE_lambdef = 28
RULE_lambdef_nocond = 29
RULE_or_test = 30
RULE_and_test = 31
RULE_not_test_2 = 32
RULE_comparison = 33
RULE_star_expr = 34
RULE_expr = 35
RULE_xor_expr = 36
RULE_and_expr = 37
RULE_shift_expr = 38
RULE_arith_expr = 39
RULE_term = 40
RULE_factor_2 = 41
RULE_power = 42
RULE_atom_expr_await = 43
RULE_atom_expr_normal = 44
RULE_atom_paren = 45
RULE_atom_bracket = 46
RULE_atom_brace = 47
RULE_trailer_paren = 48
RULE_trailer_bracket = 49
RULE_trailer_period = 50
RULE_subscriptlist = 51
RULE_subscript_2 = 52
RULE_subscript_3 = 53
RULE_testlist = 54
RULE_dictorsetmaker_item = 55
RULE_classdef = 56
RULE_yield_expr = 57
RULE_const_object = 58
RULE_generic_colon_test = 59
RULE_generic_equal_test = 60
RULE_single_input = 61
RULE_file_input_3 = 62
RULE_eval_input = 63
RULE_eval_input_2 = 64
RULE_decorator = 65
RULE_decorators = 66
RULE_decorated_body = 67
RULE_async_funcdef = 68
RULE_funcdefrettype = 69
RULE_typedargslist = 70
RULE_typedargslist_item = 71
RULE_typedargslist_name = 72
RULE_typedargslist_star = 73
RULE_typedargslist_dbl_star = 74
RULE_tfpdef = 75
RULE_varargslist = 76
RULE_varargslist_item = 77
RULE_varargslist_name = 78
RULE_varargslist_star = 79
RULE_varargslist_dbl_star = 80
RULE_vfpdef = 81
RULE_stmt = 82
RULE_simple_stmt = 83
RULE_small_stmt = 84
RULE_expr_stmt_2 = 85
RULE_expr_stmt_augassign = 86
RULE_expr_stmt_assign_list = 87
RULE_expr_stmt_assign = 88
RULE_expr_stmt_6 = 89
RULE_testlist_star_expr_2 = 90
RULE_annassign = 91
RULE_augassign = 92
RULE_flow_stmt = 93
RULE_raise_stmt_arg = 94
RULE_raise_stmt_from = 95
RULE_import_stmt = 96
RULE_import_from_2 = 97
RULE_import_from_2b = 98
RULE_import_from_3 = 99
RULE_import_as_names_paren = 100
RULE_one_or_more_period_or_ellipsis = 101
RULE_period_or_ellipsis = 102
RULE_import_as_name = 103
RULE_dotted_as_name = 104
RULE_as_name = 105
RULE_import_as_names = 106
RULE_dotted_as_names = 107
RULE_dotted_name = 108
RULE_name_list = 109
RULE_assert_stmt_extra = 110
RULE_compound_stmt = 111
RULE_async_stmt_2 = 112
RULE_if_stmt_elif_list = 113
RULE_if_stmt_elif = 114
RULE_try_stmt_2 = 115
RULE_try_stmt_except_and_more = 116
RULE_try_stmt_except = 117
RULE_try_stmt_as_name = 118
RULE_try_stmt_except_list = 119
RULE_try_stmt_finally = 120
RULE_else_stmt = 121
RULE_with_stmt_list = 122
RULE_with_item = 123
RULE_with_item_as = 124
RULE_suite = 125
RULE_suite_block = 126
RULE_namedexpr_test_2 = 127
RULE_test = 128
RULE_test_if_else = 129
RULE_test_nocond = 130
RULE_not_test = 131
RULE_comp_op = 132
RULE_comp_op_not_in = 133
RULE_comp_op_is = 134
RULE_comp_op_is_not = 135
RULE_shift_op = 136
RULE_arith_op = 137
RULE_term_op = 138
RULE_factor = 139
RULE_factor_op = 140
RULE_atom_expr = 141
RULE_atom_expr_trailers = 142
RULE_power_dbl_star = 143
RULE_atom = 144
RULE_atom_2b = 145
RULE_testlist_comp = 146
RULE_testlist_comp_2 = 147
RULE_testlist_comp_3 = 148
RULE_testlist_comp_3b = 149
RULE_testlist_comp_3c = 150
RULE_trailer = 151
RULE_subscript = 152
RULE_subscript_3b = 153
RULE_subscript_3c = 154
RULE_subscript_3d = 155
RULE_sliceop = 156
RULE_exprlist = 157
RULE_exprlist_2 = 158
RULE_dictorsetmaker = 159
RULE_dictorsetmaker_tail = 160
RULE_dictorsetmaker_list = 161
RULE_dictorsetmaker_list2 = 162
RULE_classdef_2 = 163
RULE_arglist = 164
RULE_arglist_2 = 165
RULE_arglist_star = 166
RULE_arglist_dbl_star = 167
RULE_argument = 168
RULE_argument_2 = 169
RULE_argument_3 = 170
RULE_comp_iter = 171
RULE_comp_for = 172
RULE_comp_if = 173
RULE_yield_arg = 174
RULE_yield_arg_from = 175

RULE_COUNT = 176

RULE_NAMES = (
    'file_input',
    'file_input_2',
    'decorated',
    'funcdef',
    'simple_stmt_2',
    'expr_stmt',
    'testlist_star_expr',
    'del_stmt',
    'pass_stmt',
    'break_stmt',
    'continue_stmt',
    'return_stmt',
    'yield_stmt',
    'raise_stmt',
    'import_name',
    'import_from',
    'global_stmt',
    'nonlocal_stmt',
    'assert_stmt',
    'async_stmt',
    'if_stmt',
    'while_stmt',
    'for_stmt',
    'try_stmt',
    'with_stmt',
    'suite_block_stmts',
    'namedexpr_test',
    'test_if_expr',
    'lambdef',
    'lambdef_nocond',
    'or_test',
    'and_test',
    'not_test_2',
    'comparison',
    'star_expr',
    'expr',
    'xor_expr',
    'and_expr',
    'shift_expr',
    'arith_expr',
    'term',
    'factor_2',
    'power',
    'atom_expr_await',
    'atom_expr_normal',
    'atom_paren',
    'atom_bracket',
    'atom_brace',
    'trailer_paren',
    'trailer_bracket',
    'trailer_period',
    'subscriptlist',
    'subscript_2',
    'subscript_3',
    'testlist',
    'dictorsetmaker_item',
    'classdef',
    'yield_expr',
    'RULE_const_object',
    'generic_colon_test',
    'generic_equal_test',
    'single_input',
    'file_input_3',
    'eval_input',
    'eval_input_2',
    'decorator',
    'decorators',
    'decorated_body',
    'async_funcdef',
    'funcdefrettype',
    'typedargslist',
    'typedargslist_item',
    'typedargslist_name',
    'typedargslist_star',
    'typedargslist_dbl_star',
    'tfpdef',
    'varargslist',
    'varargslist_item',
    'varargslist_name',
    'varargslist_star',
    'varargslist_dbl_star',
    'vfpdef',
    'stmt',
    'simple_stmt',
    'small_stmt',
    'expr_stmt_2',
    'expr_stmt_augassign',
    'expr_stmt_assign_list',
    'expr_stmt_assign',
    'expr_stmt_6',
    'testlist_star_expr_2',
    'annassign',
    'augassign',
    'flow_stmt',
    'raise_stmt_arg',
    'raise_stmt_from',
    'import_stmt',
    'import_from_2',
    'import_from_2b',
    'import_from_3',
    'import_as_names_paren',
    'one_or_more_period_or_ellipsis',
    'period_or_ellipsis',
    'import_as_name',
    'dotted_as_name',
    'as_name',
    'import_as_names',
    'dotted_as_names',
    'dotted_name',
    'name_list',
    'assert_stmt_extra',
    'compound_stmt',
    'async_stmt_2',
    'if_stmt_elif_list',
    'if_stmt_elif',
    'try_stmt_2',
    'try_stmt_except_and_more',
    'try_stmt_except',
    'try_stmt_as_name',
    'try_stmt_except_list',
    'try_stmt_finally',
    'else_stmt',
    'with_stmt_list',
    'with_item',
    'with_item_as',
    'suite',
    'suite_block',
    'namedexpr_test_2',
    'test',
    'test_if_else',
    'test_nocond',
    'not_test',
    'comp_op',
    'comp_op_not_in',
    'comp_op_is',
    'comp_op_is_not',
    'shift_op',
    'arith_op',
    'term_op',
    'factor',
    'factor_op',
    'atom_expr',
    'atom_expr_trailers',
    'power_dbl_star',
    'atom',
    'atom_2b',
    'testlist_comp',
    'testlist_comp_2',
    'testlist_comp_3',
    'testlist_comp_3b',
    'testlist_comp_3c',
    'trailer',
    'subscript',
    'subscript_3b',
    'subscript_3c',
    'subscript_3d',
    'sliceop',
    'exprlist',
    'exprlist_2',
    'dictorsetmaker',
    'dictorsetmaker_tail',
    'dictorsetmaker_list',
    'dictorsetmaker_list2',
    'classdef_2',
    'arglist',
    'arglist_2',
    'arglist_star',
    'arglist_dbl_star',
    'argument',
    'argument_2',
    'argument_3',
    'comp_iter',
    'comp_for',
    'comp_if',
    'yield_arg',
    'yield_arg_from',
)

# Rules carrying a compile function come first; this is where that
# block ends, and equals RULE_const_object.
FIRST_RULE_WITHOUT_COMPILE = 59
NUM_RULES_WITH_COMPILE = 58

# -- tables --------------------------------------------------------
# One action byte per rule.
RULE_ACT = bytes((
    0x61, 0x32, 0x62, 0xa8, 0x33, 0x22, 0x33, 0x22, 0x21, 0x21, 0x21, 0x22,
    0x21, 0x22, 0x22, 0x24, 0x22, 0x22, 0x23, 0x22, 0x26, 0x25, 0x27, 0x24,
    0x24, 0x32, 0x62, 0x62, 0xa4, 0xa4, 0x31, 0x31, 0x22, 0x31, 0x22, 0x31,
    0x31, 0x31, 0x31, 0x31, 0x31, 0x62, 0x62, 0x23, 0x62, 0x23, 0x23, 0x23,
    0x23, 0x23, 0x22, 0x33, 0x62, 0x22, 0x33, 0x62, 0xa5, 0x22, 0x00, 0x62,
    0x62, 0x13, 0x12, 0x62, 0x21, 0x24, 0x32, 0x13, 0x22, 0x62, 0x33, 0x13,
    0x63, 0x22, 0x23, 0x22, 0x33, 0x13, 0x62, 0x22, 0x22, 0x61, 0x12, 0x62,
    0x18, 0x13, 0x62, 0x32, 0x62, 0x12, 0x12, 0x23, 0x1d, 0x15, 0x62, 0x62,
    0x12, 0x12, 0x62, 0x13, 0x63, 0x32, 0x12, 0x22, 0x62, 0x62, 0x33, 0x31,
    0x31, 0x31, 0x62, 0x19, 0x13, 0x32, 0x24, 0x12, 0x63, 0x24, 0x62, 0x32,
    0x23, 0x63, 0x31, 0x62, 0x62, 0x12, 0x64, 0x62, 0x12, 0x24, 0x12, 0x12,
    0x19, 0x22, 0x22, 0x21, 0x12, 0x12, 0x15, 0x12, 0x13, 0x12, 0x32, 0x62,
    0x1c, 0x12, 0x62, 0x12, 0x12, 0x62, 0x33, 0x13, 0x12, 0x12, 0x22, 0x62,
    0x22, 0x33, 0x12, 0x62, 0x12, 0x22, 0x33, 0x63, 0x33, 0x13, 0x22, 0x22,
    0x62, 0x13, 0x22, 0x12, 0xa5, 0x23, 0x12, 0x22,
))

# All rules' argument words, concatenated; RULE_ARG_OFFSET[i] is where
# rule i's arguments begin.  Flat arrays rather than a list of tuples:
# 2 bytes per word instead of a pointer plus an object each.
RULE_ARG = array('H', (
    0x3001, 0x203e, 0x2042, 0x2043, 0x101a, 0x1008, 0x1054, 0x3046, 0x1055, 0x3045,
    0x105b, 0x207d, 0x2054, 0x105d, 0x2006, 0x3055, 0x205a, 0x105a, 0x101b, 0x209d,
    0x102b, 0x1017, 0x1019, 0x102d, 0x3036, 0x2039, 0x102c, 0x305e, 0x1024, 0x206b,
    0x1021, 0x2061, 0x1024, 0x2063, 0x1022, 0x206d, 0x1028, 0x206d, 0x1014, 0x2080,
    0x306e, 0x1015, 0x2070, 0x1023, 0x201a, 0x105b, 0x207d, 0x3071, 0x3079, 0x102f,
    0x201a, 0x105b, 0x207d, 0x3079, 0x1020, 0x209d, 0x1025, 0x2036, 0x105b, 0x207d,
    0x3079, 0x102e, 0x105b, 0x207d, 0x2073, 0x1030, 0x207a, 0x105b, 0x207d, 0x2052,
    0x2080, 0x307f, 0x201e, 0x3081, 0x1027, 0x304c, 0x105b, 0x2080, 0x1027, 0x304c,
    0x105b, 0x2082, 0x201f, 0x102a, 0x2083, 0x1012, 0x1029, 0x2083, 0x2023, 0x2084,
    0x1041, 0x2023, 0x2024, 0x103a, 0x2025, 0x103b, 0x2026, 0x103c, 0x2027, 0x2088,
    0x2028, 0x2089, 0x208b, 0x208a, 0x208c, 0x208b, 0x208d, 0x308f, 0x1016, 0x2090,
    0x308e, 0x2090, 0x308e, 0x1054, 0x3091, 0x1055, 0x1056, 0x3092, 0x1057, 0x1058,
    0x309f, 0x1059, 0x1054, 0x30a4, 0x1055, 0x1056, 0x2033, 0x1057, 0x105c, 0x1008,
    0x2098, 0x105a, 0x2080, 0x3035, 0x105b, 0x3099, 0x2080, 0x105a, 0x2080, 0x303b,
    0x1018, 0x1008, 0x30a3, 0x105b, 0x207d, 0x1031, 0x30ae, 0x105b, 0x2080, 0x105e,
    0x2080, 0x1005, 0x2053, 0x206f, 0x1005, 0x2052, 0x2036, 0x3040, 0x1005, 0x1042,
    0x206c, 0x3030, 0x1005, 0x2041, 0x2038, 0x2003, 0x2044, 0x1015, 0x2003, 0x105f,
    0x2080, 0x2047, 0x105a, 0x2048, 0x2049, 0x204a, 0x1008, 0x303b, 0x303c, 0x1041,
    0x304b, 0x1046, 0x1008, 0x303b, 0x1008, 0x303b, 0x204d, 0x105a, 0x204e, 0x204f,
    0x2050, 0x1008, 0x303c, 0x1041, 0x3051, 0x1046, 0x1008, 0x1008, 0x206f, 0x2053,
    0x2004, 0x1005, 0x2007, 0x2008, 0x205d, 0x2060, 0x2010, 0x2011, 0x2012, 0x2005,
    0x205b, 0x2056, 0x2057, 0x205c, 0x2059, 0x2058, 0x105e, 0x2059, 0x2039, 0x2006,
    0x2022, 0x2080, 0x105b, 0x2080, 0x3058, 0x104c, 0x104d, 0x104e, 0x104f, 0x1051,
    0x1052, 0x1049, 0x1047, 0x1048, 0x104a, 0x104b, 0x1053, 0x1050, 0x2009, 0x200a,
    0x200b, 0x200d, 0x200c, 0x2080, 0x305f, 0x1021, 0x2080, 0x200e, 0x200f, 0x206c,
    0x2062, 0x2065, 0x306c, 0x1041, 0x2064, 0x206a, 0x1054, 0x206a, 0x1055, 0x2066,
    0x105c, 0x100d, 0x1008, 0x3069, 0x206c, 0x3069, 0x1013, 0x1008, 0x2067, 0x105a,
    0x2068, 0x105a, 0x1008, 0x105c, 0x1008, 0x105a, 0x105a, 0x2080, 0x2014, 0x2015,
    0x2016, 0x2017, 0x2018, 0x2003, 0x2038, 0x2002, 0x2013, 0x2003, 0x2018, 0x2016,
    0x2072, 0x101c, 0x201a, 0x105b, 0x207d, 0x2074, 0x2078, 0x2077, 0x3079, 0x3078,
    0x101e, 0x3076, 0x105b, 0x207d, 0x2080, 0x3069, 0x2075, 0x101f, 0x105b, 0x207d,
    0x101d, 0x105b, 0x207d, 0x207b, 0x105a, 0x2080, 0x307c, 0x1013, 0x2023, 0x207e,
    0x2053, 0x1005, 0x1006, 0x2019, 0x1007, 0x1032, 0x2080, 0x201c, 0x201b, 0x1023,
    0x201e, 0x101d, 0x2080, 0x201d, 0x201e, 0x2020, 0x2021, 0x1034, 0x1035, 0x1036,
    0x1037, 0x1038, 0x1039, 0x1025, 0x2085, 0x2086, 0x1029, 0x1025, 0x1026, 0x3087,
    0x1029, 0x103d, 0x103e, 0x103f, 0x1040, 0x1041, 0x1042, 0x1044, 0x1045, 0x1043,
    0x2029, 0x202a, 0x103f, 0x1040, 0x1033, 0x202b, 0x202c, 0x2097, 0x1046, 0x208b,
    0x1008, 0x1009, 0x100a, 0x100b, 0x100c, 0x100d, 0x100f, 0x1010, 0x100e, 0x202d,
    0x202e, 0x202f, 0x2039, 0x2092, 0x2093, 0x3094, 0x2022, 0x201a, 0x20ac, 0x2095,
    0x105a, 0x3096, 0x2093, 0x105a, 0x2030, 0x2031, 0x2032, 0x2035, 0x2034, 0x209a,
    0x209b, 0x105b, 0x3080, 0x2080, 0x309c, 0x105b, 0x3080, 0x209e, 0x105a, 0x2022,
    0x2023, 0x2037, 0x30a0, 0x20ac, 0x20a1, 0x105a, 0x30a2, 0x2037, 0x105a, 0x1054,
    0x30a4, 0x1055, 0x20a5, 0x105a, 0x20a6, 0x20a7, 0x20a8, 0x1041, 0x2080, 0x1046,
    0x2080, 0x2080, 0x30a9, 0x20ac, 0x203c, 0x20aa, 0x1032, 0x2080, 0x20ac, 0x20ad,
    0x1020, 0x209d, 0x1025, 0x201e, 0x30ab, 0x1023, 0x2082, 0x30ab, 0x20af, 0x2036,
    0x1021, 0x2080,
))

RULE_ARG_OFFSET = array('H', (
    0, 1, 2, 4, 12, 14, 16, 18, 20, 21, 22, 23,
    25, 26, 28, 30, 34, 36, 38, 41, 43, 49, 54, 61,
    65, 69, 70, 72, 74, 78, 82, 84, 86, 88, 90, 92,
    94, 96, 98, 100, 102, 104, 106, 108, 111, 113, 116, 119,
    122, 125, 128, 130, 132, 134, 136, 138, 140, 145, 147, 147,
    149, 151, 154, 156, 158, 159, 163, 164, 167, 169, 171, 173,
    176, 179, 181, 184, 186, 188, 191, 193, 195, 197, 198, 200,
    202, 210, 213, 215, 216, 218, 220, 222, 225, 238, 243, 245,
    247, 249, 251, 253, 256, 259, 260, 262, 264, 266, 268, 270,
    272, 274, 276, 278, 287, 290, 291, 295, 297, 300, 304, 306,
    307, 310, 313, 315, 317, 319, 321, 325, 327, 329, 333, 335,
    337, 346, 348, 350, 351, 353, 355, 360, 362, 365, 367, 368,
    370, 382, 384, 386, 388, 390, 392, 394, 397, 399, 401, 403,
    405, 407, 409, 411, 413, 415, 417, 419, 422, 424, 427, 429,
    431, 433, 436, 438, 440, 445, 448, 450, 452,
))


def rule_act(rule_id):
    return RULE_ACT[rule_id]


def rule_arg_count(rule_id):
    """How many argument words rule *rule_id* has."""
    return RULE_ARG_OFFSET[rule_id + 1] - RULE_ARG_OFFSET[rule_id]


def rule_arg(rule_id, i):
    """Argument word *i* of rule *rule_id*."""
    return RULE_ARG[RULE_ARG_OFFSET[rule_id] + i]


def rule_name(rule_id):
    if 0 <= rule_id < RULE_COUNT:
        return RULE_NAMES[rule_id]
    return "RULE_?%d" % rule_id
