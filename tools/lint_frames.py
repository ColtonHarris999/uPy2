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
    * ``obj.m(...)`` resolves to *every* method named ``m``, since the
      receiver's type is unknown.
    """

    def __init__(self, path, defs, calls, classes):
        self.path = path
        self.defs = defs
        self.calls = calls
        self.classes = classes
        self.stack = []

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
        attrs = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    named.add(f.id)
                elif isinstance(f, ast.Attribute):
                    attrs.add(f.attr)
        self.calls[qual] = (named, attrs)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._function(node)

    def visit_AsyncFunctionDef(self, node):
        self._function(node)


def collect(package_dir):
    defs = {}
    raw_calls = {}
    classes = set()
    for root, dirs, files in os.walk(package_dir):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            with open(path, "r") as fh:
                src = fh.read()
            tree = ast.parse(src, path)
            Collector(path, defs, raw_calls, classes).visit(tree)

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
        named, attrs = raw_calls[qual]
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
        for a in attrs:
            if a == "__init__":
                # `Base.__init__(self, ...)` goes up the hierarchy, never
                # into one of our own methods; resolving it by name would
                # make every constructor look self-recursive.
                continue
            for t in methods.get(a, ()):
                out.add(t)
        calls[qual] = out
    return defs, calls


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
    defs, calls = collect(package)
    print("defs   : %d functions" % len(defs))

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
