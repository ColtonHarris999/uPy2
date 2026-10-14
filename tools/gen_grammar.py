"""Generate ``mpyc/grammar.py``: MicroPython's grammar tables.

``py/grammar.h`` holds 182 rules in a macro DSL that the C preprocessor turns
into three parallel tables in ``py/parse.c``.  This reproduces those tables.

Two things make the numbering load-bearing rather than incidental:

* A struct parse node's *kind* is its rule index, so the numbering is part of
  the data format the compiler reads.
* ``parse.c`` orders the rules as: every ``DEF_RULE`` (those with a compile
  function) in file order, then ``RULE_const_object``, then every
  ``DEF_RULE_NC``.  Getting that split wrong shifts every kind.

Rule actions and argument words use the same bit layout as the C tables, so
the engine can be read side by side with ``parse.c``.

    python3 tools/gen_grammar.py /path/to/micropython-v1.29.0

Needs ``mpyc/tokens.py`` to already be generated, since arguments reference
tokens by number.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Bit layout, from py/parse.c.
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


def split_args(text):
    """Split a macro argument list on top-level commas."""
    out = []
    depth = 0
    cur = []
    for ch in text:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    last = "".join(cur).strip()
    if last:
        out.append(last)
    return out


def parse_grammar(path, flags):
    """[(name, comp_or_None, action_text, [arg_text, ...])] in file order."""
    rules = []
    emitting = [True]
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("#if"):
                cond = line[3:].strip()
                value = flags.get(cond)
                if value is None:
                    raise SystemExit("unknown condition %r in grammar.h; add "
                                     "it to the flag table" % cond)
                emitting.append(emitting[-1] and value)
                continue
            if line.startswith("#else"):
                emitting[-1] = emitting[-2] and not emitting[-1]
                continue
            if line.startswith("#endif"):
                emitting.pop()
                continue
            if not emitting[-1]:
                continue
            m = re.match(r"^DEF_RULE(_NC)?\((.*)\)\s*$", line)
            if not m:
                continue
            nc = m.group(1) is not None
            parts = split_args(m.group(2))
            name = parts[0]
            if nc:
                comp = None
                action = parts[1]
                args = parts[2:]
            else:
                comp = parts[1]
                action = parts[2]
                args = parts[3:]
            rules.append((name, comp, action, args))
    return rules


def encode_action(text):
    m = re.match(r"^(or|and|and_ident|and_blank)\((\d+)\)$", text)
    if m:
        kind, n = m.group(1), int(m.group(2))
        if n > RULE_ACT_ARG_MASK:
            raise SystemExit("rule arity %d does not fit the action field" % n)
        if kind == "or":
            return RULE_ACT_OR | n
        if kind == "and":
            return RULE_ACT_AND | n
        if kind == "and_ident":
            return RULE_ACT_AND | n | RULE_ACT_ALLOW_IDENT
        return RULE_ACT_AND | n | RULE_ACT_ADD_BLANK
    if text == "one_or_more":
        return RULE_ACT_LIST | 2
    if text == "list":
        return RULE_ACT_LIST | 1
    if text == "list_with_end":
        return RULE_ACT_LIST | 3
    raise SystemExit("unknown rule action %r" % text)


def encode_arg(text, rule_index, tokens):
    m = re.match(r"^tok\((\w+)\)$", text)
    if m:
        name = "MP_TOKEN_" + m.group(1)
        value = getattr(tokens, name, None)
        if value is None:
            raise SystemExit("grammar references unknown token %s" % name)
        return RULE_ARG_TOK | value
    m = re.match(r"^rule\((\w+)\)$", text)
    if m:
        return RULE_ARG_RULE | rule_index[m.group(1)]
    m = re.match(r"^opt_rule\((\w+)\)$", text)
    if m:
        return RULE_ARG_OPT_RULE | rule_index[m.group(1)]
    raise SystemExit("unknown rule argument %r" % text)


def render(rules, order, rule_index, tokens, tree_root, flags):
    acts = []
    args_flat = []
    offsets = []
    for name in order:
        if name == "RULE_const_object":
            acts.append(0)
            offsets.append(len(args_flat))
            continue
        rule = rules[name]
        acts.append(encode_action(rule[2]))
        offsets.append(len(args_flat))
        for a in rule[3]:
            args_flat.append(encode_arg(a, rule_index, tokens))
    offsets.append(len(args_flat))

    lines = []
    w = lines.append
    w('"""MicroPython\'s grammar tables -- GENERATED, do not edit.')
    w("")
    w("Regenerate with ``tools/gen_grammar.py <micropython-source-tree>``.")
    w("")
    w("A struct parse node\'s kind is its rule index, so this numbering is part")
    w("of the format the compiler reads.  ``py/parse.c`` orders the rules as")
    w("every DEF_RULE in file order, then RULE_const_object, then every")
    w("DEF_RULE_NC; that split is reproduced here.")
    w('"""')
    w("")
    w("from array import array")
    w("")
    w("# Generated from %s" % os.path.basename(os.path.abspath(tree_root)))
    w("# Configuration:")
    for k in sorted(flags):
        w("#   %s = %d" % (k, 1 if flags[k] else 0))
    w("")
    w("# -- bit layout (py/parse.c) ---------------------------------------")
    w("RULE_ACT_ARG_MASK = 0x0F")
    w("RULE_ACT_KIND_MASK = 0x30")
    w("RULE_ACT_ALLOW_IDENT = 0x40")
    w("RULE_ACT_ADD_BLANK = 0x80")
    w("RULE_ACT_OR = 0x10")
    w("RULE_ACT_AND = 0x20")
    w("RULE_ACT_LIST = 0x30")
    w("")
    w("RULE_ARG_KIND_MASK = 0xF000")
    w("RULE_ARG_ARG_MASK = 0x0FFF")
    w("RULE_ARG_TOK = 0x1000")
    w("RULE_ARG_RULE = 0x2000")
    w("RULE_ARG_OPT_RULE = 0x3000")
    w("")
    w("# -- rule numbering ------------------------------------------------")
    for i, name in enumerate(order):
        const = name if name == "RULE_const_object" else "RULE_" + name
        w("%s = %d" % (const, i))
    w("")
    w("RULE_COUNT = %d" % len(order))
    w("")
    w("RULE_NAMES = (")
    for name in order:
        w("    %r," % (name if name == "RULE_const_object" else name))
    w(")")
    w("")
    w("# Rules carrying a compile function come first; this is where that")
    w("# block ends, and equals RULE_const_object.")
    w("FIRST_RULE_WITHOUT_COMPILE = %d" % (order.index("RULE_const_object") + 1))
    w("NUM_RULES_WITH_COMPILE = %d" % order.index("RULE_const_object"))
    w("")
    w("# -- tables --------------------------------------------------------")
    w("# One action byte per rule.")
    w("RULE_ACT = bytes((")
    for i in range(0, len(acts), 12):
        w("    " + " ".join("0x%02x," % a for a in acts[i:i + 12]))
    w("))")
    w("")
    w("# All rules' argument words, concatenated; RULE_ARG_OFFSET[i] is where")
    w("# rule i's arguments begin.  Flat arrays rather than a list of tuples:")
    w("# 2 bytes per word instead of a pointer plus an object each.")
    w("RULE_ARG = array('H', (")
    for i in range(0, len(args_flat), 10):
        w("    " + " ".join("0x%04x," % a for a in args_flat[i:i + 10]))
    w("))")
    w("")
    w("RULE_ARG_OFFSET = array('H', (")
    for i in range(0, len(offsets), 12):
        w("    " + " ".join("%d," % o for o in offsets[i:i + 12]))
    w("))")
    w("")
    w("")
    w("def rule_act(rule_id):")
    w("    return RULE_ACT[rule_id]")
    w("")
    w("")
    w("def rule_arg_count(rule_id):")
    w('    """How many argument words rule *rule_id* has."""')
    w("    return RULE_ARG_OFFSET[rule_id + 1] - RULE_ARG_OFFSET[rule_id]")
    w("")
    w("")
    w("def rule_arg(rule_id, i):")
    w('    """Argument word *i* of rule *rule_id*."""')
    w("    return RULE_ARG[RULE_ARG_OFFSET[rule_id] + i]")
    w("")
    w("")
    w("def rule_name(rule_id):")
    w("    if 0 <= rule_id < RULE_COUNT:")
    w("        return RULE_NAMES[rule_id]")
    w('    return "RULE_?%d" % rule_id')
    return "\n".join(lines) + "\n"


def main(argv):
    flags = {
        "MICROPY_PY_FSTRINGS": True,
        "MICROPY_PY_TSTRINGS": True,
        "MICROPY_PY_ASYNC_AWAIT": True,
        "MICROPY_PY_ASSIGN_EXPR": True,
        "MICROPY_PY_BUILTINS_SET": True,
        "MICROPY_PY_BUILTINS_SLICE": True,
        "MICROPY_COMP_FSTRING_LITERAL": True,
    }
    tree_root = None
    out_path = None
    it = iter(argv)
    for a in it:
        if a == "-o":
            out_path = next(it)
        elif a.startswith("--no-"):
            key = "MICROPY_PY_" + a[5:].upper().replace("-", "_")
            if key not in flags:
                raise SystemExit("unknown flag %s" % a)
            flags[key] = False
        else:
            tree_root = a
    if tree_root is None:
        print(__doc__)
        return 2
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if out_path is None:
        out_path = os.path.join(here, "mpyc", "grammar.py")

    from mpyc import tokens

    parsed = parse_grammar(os.path.join(tree_root, "py", "grammar.h"), flags)
    rules = {}
    with_compile = []
    without_compile = []
    for name, comp, action, args in parsed:
        if name in rules:
            raise SystemExit("duplicate rule %s" % name)
        rules[name] = (name, comp, action, args)
        if comp is None:
            without_compile.append(name)
        else:
            with_compile.append(name)

    # The order py/parse.c builds its tables in.
    order = with_compile + ["RULE_const_object"] + without_compile
    rule_index = {}
    for i, name in enumerate(order):
        rule_index[name] = i

    # Every referenced rule must exist, or a kind would silently be wrong.
    for name, comp, action, args in parsed:
        for a in args:
            m = re.match(r"^(?:opt_)?rule\((\w+)\)$", a)
            if m and m.group(1) not in rule_index:
                raise SystemExit("rule %s references undefined rule %s"
                                 % (name, m.group(1)))
    # An "and"/"or" rule's declared arity must match its argument count.
    for name, comp, action, args in parsed:
        m = re.match(r"^(or|and|and_ident|and_blank)\((\d+)\)$", action)
        if m and int(m.group(2)) != len(args):
            raise SystemExit("rule %s declares %s but has %d arguments"
                             % (name, action, len(args)))

    text = render(rules, order, rule_index, tokens, tree_root, flags)
    with open(out_path, "w") as f:
        f.write(text)
    print("rules           : %d (%d with a compile function, %d without)"
          % (len(order), len(with_compile), len(without_compile)))
    print("RULE_const_object: %d" % order.index("RULE_const_object"))
    print("wrote           : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
