"""Frame-depth linter: enforce the target's call-stack budget statically.

The target's call stack is a fixed pool of hardware-managed frames carved out
of the same 256 KB SRAM as the heap, and each frame also holds that
function's Python slots.  It cannot be raised, and overflow surfaces as
``RuntimeError: maximum recursion depth exceeded``.  The compiler therefore
has a hard budget -- currently 32 frames -- for its own Python calls.

Unlike a heap limit, this one is checkable without running anything: the
shipped package is pure Python we control, so its call graph is static.  This
tool builds that graph and fails if

* any cycle exists (recursion is banned outright -- nested parsing and code
  generation belong on an explicit stack in the heap), or
* the longest call chain exceeds the budget.

Call resolution is deliberately over-approximate: a call is matched to every
definition sharing its name, so the reported depth is an upper bound.  Being
wrong in that direction is safe.

This is tooling, not shipped code, so it may use ``ast``.

    python tools/lint_frames.py [--budget N] [--package DIR]
"""
import ast
import os
import sys

DEFAULT_BUDGET = 32


class Collector(ast.NodeVisitor):
    """Collect every function definition, qualified by its class, and the
    calls each one makes.

    Definitions are keyed by qualified name (``ParseTree.new_struct``) rather
    than bare name, because several classes define ``__init__`` and merging
    them invents call cycles that do not exist.

    Calls resolve in two ways, both deliberately over-approximate -- an upper
    bound on depth is the safe direction:

    * ``Name(...)`` resolves to a module-level function of that name, or to
      the ``__init__`` of a class of that name (a constructor call).
    * ``self.m(...)`` resolves to the enclosing class's own ``m``.
    * ``obj.m(...)``, where ``obj`` is anything else, resolves to every
      method named ``m`` *except* the enclosing class's: a receiver spelled
      something other than ``self`` is a different object, and nothing here
      hands a method another instance of its own class.  Without that, a
      method that delegates to a same-named method on a member -- as
      ``QstrTable.text`` does to the pool's -- reads as self-recursive.
    * A call through a table, ``self.TABLE[i](...)``, resolves to whatever
      ``self.TABLE = (self.a, self.b, ...)`` put in it.  The walker
      dispatches its work items that way, and without this the deepest
      chains in the compiler would be invisible.
    * A table built with ``getattr`` in the same function that assigns it --
      ``self.TABLE = tuple(getattr(self.obj, n) for n in NAMES)``, or a loop
      that appends ``getattr(self, "h_" + name)`` -- resolves to the methods
      of the receiver's class, narrowed to a literal name prefix when the
      ``getattr`` has one.  The receiver's class comes from ``self`` itself,
      or from an earlier ``self.obj = SomeClass(...)``.

    Indirect calls it still cannot resolve, and tables it cannot see into,
    are counted and reported, so the gap is visible rather than silent.
    """

    def __init__(self, path, defs, calls, classes, tables, table_sources,
                 unresolved, class_names):
        self.path = path
        self.defs = defs
        self.calls = calls
        self.classes = classes
        self.tables = tables
        self.table_sources = table_sources
        self.unresolved = unresolved
        self.class_names = class_names
        self.attr_types = {}
        self.stack = []

    def _record_table(self, node, sources):
        """Learn what a ``self.NAME = ...`` assignment holds.

        *sources* are the ``getattr`` receivers seen anywhere in the same
        function, used when the assigned value is not a literal.
        """
        if len(node.targets) != 1:
            return
        name = _table_name(node.targets[0])
        if name is None:
            return
        value = node.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in self.class_names):
            return
        if isinstance(value, (ast.Tuple, ast.List)):
            members = self.tables.setdefault(name, set())
            explicit = False
            for elt in value.elts:
                if isinstance(elt, ast.Attribute):
                    members.add(elt.attr)
                    explicit = True
            if explicit:
                return
        for src in sources:
            self.table_sources.setdefault(name, set()).add(src)

    def _record_attr_type(self, node):
        """``self.obj = SomeClass(...)`` -> remember what ``self.obj`` is."""
        if len(node.targets) != 1:
            return
        name = _table_name(node.targets[0])
        if name is None:
            return
        value = node.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in self.class_names):
            self.attr_types[name] = value.func.id

    def _getattr_sources(self, node):
        """``(class name, name prefix or None)`` for each getattr receiver."""
        out = set()
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "getattr" and sub.args):
                continue
            recv = sub.args[0]
            cls = None
            if isinstance(recv, ast.Name) and recv.id == "self":
                cls = self.stack[-1] if self.stack else None
            else:
                holder = _table_name(recv)
                if holder is not None:
                    cls = self.attr_types.get(holder)
            if cls is None:
                continue
            prefix = None
            if len(sub.args) > 1:
                arg = sub.args[1]
                if (isinstance(arg, ast.BinOp)
                        and isinstance(arg.op, ast.Add)
                        and isinstance(arg.left, ast.Constant)
                        and isinstance(arg.left.value, str)):
                    prefix = arg.left.value
            out.add((cls, prefix))
        return out

    def _qual(self, name):
        return ".".join(self.stack + [name])

    def visit_ClassDef(self, node):
        self.classes.add(self._qual(node.name))
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def _function(self, node):
        qual = self._qual(node.name)
        self.defs[qual] = (self.path, node.lineno, node.name)
        named = set()
        self_attrs = set()
        other_attrs = set()
        tables = set()
        # Two passes over the body: the first learns the types of the
        # attributes assigned here, which the second needs to tell what a
        # getattr-built table holds.
        assigns = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                assigns.append(sub)
                self._record_attr_type(sub)
        sources = self._getattr_sources(node)
        for sub in assigns:
            self._record_table(sub, sources)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    named.add(f.id)
                elif isinstance(f, ast.Attribute):
                    if (isinstance(f.value, ast.Name)
                            and f.value.id == "self"):
                        self_attrs.add(f.attr)
                    else:
                        other_attrs.add(f.attr)
                elif isinstance(f, ast.Subscript):
                    name = _table_name(f.value)
                    if name is None:
                        self.unresolved.append(
                            (self.path, sub.lineno, qual))
                    else:
                        tables.add(name)
                else:
                    self.unresolved.append((self.path, sub.lineno, qual))
        self.calls[qual] = (named, self_attrs, other_attrs, tables)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._function(node)

    def visit_AsyncFunctionDef(self, node):
        self._function(node)


def _table_name(node):
    """``self.NAME`` as a plain name, or None."""
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    return None


def collect(package_dir):
    defs = {}
    raw_calls = {}
    classes = set()
    tables = {}
    table_sources = {}
    unresolved = []
    # Class names, to tell `self.x = Emit(...)` from `self.x = tuple(...)`.
    class_names = set()
    for root, dirs, files in os.walk(package_dir):
        for f in sorted(files):
            if f.endswith(".py"):
                with open(os.path.join(root, f), "r") as fh:
                    for sub in ast.walk(ast.parse(fh.read(), f)):
                        if isinstance(sub, ast.ClassDef):
                            class_names.add(sub.name)
    for root, dirs, files in os.walk(package_dir):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            with open(path, "r") as fh:
                src = fh.read()
            tree = ast.parse(src, path)
            Collector(path, defs, raw_calls, classes, tables,
                      table_sources, unresolved, class_names).visit(tree)

    # Index the definitions for resolution.
    plain = {}      # bare name -> [qualname] for module-level functions
    methods = {}    # method name -> [qualname]
    inits = {}      # class name -> its __init__ qualname
    for qual in defs:
        bare = defs[qual][2]
        if "." in qual:
            methods.setdefault(bare, []).append(qual)
            if bare == "__init__":
                cls = qual.rsplit(".", 2)[-2]
                inits[cls] = qual
        else:
            plain.setdefault(bare, []).append(qual)

    calls = {}
    for qual in raw_calls:
        named, self_attrs, other_attrs, call_tables = raw_calls[qual]
        # The class this function is defined in, if any.
        own_class = qual.rsplit(".", 2)[-2] + "." if "." in qual else None
        out = set()
        for n in named:
            if n in inits:
                out.add(inits[n])      # a constructor call
            for t in plain.get(n, ()):
                out.add(t)
            # A bare name may also be a method called without self in a
            # nested function; only count it if nothing else matched.
            if n not in inits and n not in plain:
                for t in methods.get(n, ()):
                    out.add(t)
        for a in self_attrs:
            if a == "__init__":
                # `self.__init__(...)` is not written here; and resolving
                # __init__ by name would make every constructor that calls a
                # base constructor look self-recursive.
                continue
            own = None
            if own_class is not None:
                for t in methods.get(a, ()):
                    if t.rsplit(".", 2)[-2] + "." == own_class:
                        own = t
            if own is not None:
                out.add(own)
            else:
                for t in methods.get(a, ()):
                    out.add(t)
        for a in other_attrs:
            if a == "__init__":
                continue
            for t in methods.get(a, ()):
                if (own_class is not None
                        and t.rsplit(".", 2)[-2] + "." == own_class):
                    continue
                out.add(t)
        for table in call_tables:
            found = 0
            # A table of explicit `self.m` references: the receiver's class
            # is unknown, so resolve by name as elsewhere.
            for a in tables.get(table, ()):
                for t in methods.get(a, ()):
                    out.add(t)
                    found += 1
            # A getattr-built table: the class *is* known, so add its own
            # methods rather than every method sharing a name -- otherwise
            # one `__init__` in the table drags in every class's.
            for cls, prefix in table_sources.get(table, ()):
                for name2 in methods:
                    if prefix is not None and not name2.startswith(prefix):
                        continue
                    for t in methods[name2]:
                        if t.rsplit(".", 2)[-2] == cls:
                            out.add(t)
                            found += 1
            if not found:
                info = defs.get(qual)
                unresolved.append((info[0] if info else "?",
                                   info[1] if info else 0,
                                   "%s (table self.%s)" % (qual, table)))
        calls[qual] = out
    return defs, calls, unresolved


def find_cycle(calls):
    """Return a cycle as a list of names, or None.  Iterative DFS."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {}
    for start in sorted(calls):
        if colour.get(start, WHITE) != WHITE:
            continue
        # stack entries: [name, iterator over callees]
        stack = [[start, None]]
        path = []
        while stack:
            top = stack[-1]
            if top[1] is None:
                if colour.get(top[0], WHITE) == BLACK:
                    stack.pop()
                    continue
                colour[top[0]] = GREY
                path.append(top[0])
                top[1] = sorted(calls.get(top[0], ()))
            if top[1]:
                nxt = top[1].pop(0)
                if nxt not in calls:
                    continue  # not ours (builtin, stdlib, method of a value)
                c = colour.get(nxt, WHITE)
                if c == GREY:
                    return path[path.index(nxt):] + [nxt]
                if c == WHITE:
                    stack.append([nxt, None])
            else:
                colour[top[0]] = BLACK
                path.pop()
                stack.pop()
    return None


def longest_chain(calls):
    """Deepest call chain, assuming the graph is acyclic.  Iterative."""
    depth = {}
    best_next = {}
    for start in sorted(calls):
        if start in depth:
            continue
        stack = [[start, None]]
        while stack:
            top = stack[-1]
            if top[1] is None:
                top[1] = [c for c in sorted(calls.get(top[0], ())) if c in calls]
            pending = None
            while top[1]:
                cand = top[1][0]
                if cand in depth:
                    top[1].pop(0)
                    continue
                pending = cand
                break
            if pending is not None:
                stack.append([pending, None])
                continue
            d = 1
            nxt = None
            for c in sorted(calls.get(top[0], ())):
                if c in depth and depth[c] + 1 > d:
                    d = depth[c] + 1
                    nxt = c
            depth[top[0]] = d
            best_next[top[0]] = nxt
            stack.pop()
    if not depth:
        return 0, []
    head = max(sorted(depth), key=lambda k: depth[k])
    chain = []
    cur = head
    while cur is not None:
        chain.append(cur)
        cur = best_next.get(cur)
    return depth[head], chain


def main(argv):
    budget = DEFAULT_BUDGET
    package = None
    it = iter(argv)
    for a in it:
        if a == "--budget":
            budget = int(next(it))
        elif a == "--package":
            package = next(it)
        else:
            package = a
    if package is None:
        here = os.path.dirname(os.path.abspath(__file__))
        package = os.path.join(os.path.dirname(here), "mpyc")
    print("package: %s" % package)
    print("budget : %d frames" % budget)
    defs, calls, unresolved = collect(package)
    print("defs   : %d functions" % len(defs))
    if unresolved:
        print("note   : %d indirect call site(s) could not be resolved;"
              " the depth below is a lower bound at those points"
              % len(unresolved))
        for path, line, qual in unresolved:
            print("         %s:%d  in %s" % (path, line, qual))

    cycle = find_cycle(calls)
    if cycle is not None:
        print("FAIL: recursion is not allowed; call cycle found:")
        print("      " + " -> ".join(cycle))
        for name in cycle:
            info = defs.get(name)
            if info is not None:
                print("        %s:%d  %s" % (info[0], info[1], name))
        print("summary: cycles=1 depth=unknown budget=%d" % budget)
        return 1

    depth, chain = longest_chain(calls)
    print("deepest: %d frames" % depth)
    if chain:
        print("      " + " -> ".join(chain))
    over = depth > budget
    print("summary: cycles=0 depth=%d budget=%d over=%d"
          % (depth, budget, 1 if over else 0))
    if over:
        print("FAIL: deepest chain exceeds the %d-frame budget" % budget)
        return 1
    print("ok: within the frame budget")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
