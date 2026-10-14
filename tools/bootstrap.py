"""Compile the compiler with itself, and check the result is a fixed point.

Three stages, each compiling the same sources with the same file names, so
that any difference in the output is a difference in the *compiler*:

* **stage 0** -- mpyc running from source on the host compiles ``mpyc/*.py``
  into ``.mpy`` files.  Each one is also compared against ``mpy-cross``, so
  stage 0 is checked against the oracle and not only against itself.
* **stage 1** -- MicroPython imports stage 0's ``.mpy`` files, so the compiler
  is now running *as its own bytecode*, on the instruction set it emits.  That
  compiler compiles the same sources again.
* **stage 2** -- the same, with stage 1's output on the path.

What the stages prove, in order:

* stage 0 == mpy-cross: the compiler can compile its own source correctly.
* stage 1 == stage 0: running as compiled bytecode does not change what it
  emits.  This is the step that would catch a bug in anything the compiler
  itself leans on -- a closure, a comprehension, a default argument, an
  exception -- because the compiler is now executing code it produced.
* stage 2 == stage 1: a fixed point.  Any further round changes nothing.

A corpus can be passed as well; stage 1 then compiles it too, and its output
is compared against stage 0's over the same files.  Self-compilation exercises
whatever the compiler's own source happens to use, which is not everything;
the corpus is what covers the rest.

    python3 tools/bootstrap.py --micropython PATH [--mpy-cross PATH] \\
        [--corpus DIR...] [--heapsize 256M]

Also runs in the compiling role, which is how the stages above invoke it under
MicroPython -- it imports whatever ``mpyc`` is on the path and compiles with
that:

    micropython tools/bootstrap.py --compile OUTDIR FILE...
"""
import os
import sys


# Where a stage records the files it refused, and why.
REJECTED = "rejected.txt"


def basename(path):
    i = len(path) - 1
    while i >= 0:
        if path[i] == "/":
            return path[i + 1:]
        i -= 1
    return path


def mpy_name(path):
    """``mpyc/compile.py`` -> ``compile.mpy``."""
    name = basename(path)
    if name.endswith(".py"):
        name = name[:len(name) - 3]
    return name + ".mpy"


def verify_loaded_from(path_dir):
    """Every mpyc module must have come from a ``.mpy`` under *path_dir*.

    Without this the stage could silently fall back to the source tree -- a
    missing ``.mpy``, or the current directory reaching the checkout -- and
    the comparison would be the compiler against itself rather than against
    its own bytecode.
    """
    checked = 0
    for name in sys.modules:
        if name != "mpyc" and not name.startswith("mpyc."):
            continue
        origin = getattr(sys.modules[name], "__file__", None)
        if origin is None:
            print("FAIL: %s has no __file__, so its origin cannot be checked"
                  % name)
            return False
        if not origin.endswith(".mpy") or not origin.startswith(path_dir):
            print("FAIL: %s came from %s, not a .mpy under %s"
                  % (name, origin, path_dir))
            return False
        checked += 1
    print("loaded %d mpyc modules, all as .mpy from %s" % (checked, path_dir))
    return checked > 0


def compile_into(out_dir, paths, path_dir=None):
    """The compiling role: compile *paths* into *out_dir*.

    Uses whichever ``mpyc`` is importable, which is the whole point -- under
    MicroPython that is a stage's ``.mpy`` files.
    """
    from mpyc import compile as C
    ok = 0
    rejected = []
    for path in paths:
        f = open(path, "rb")
        src = f.read()
        f.close()
        # The source *name* goes into the qstr table, so it has to be the
        # same string at every stage or the output would differ for that
        # reason alone.
        try:
            data = C.compile_source(src, basename(path))
        except Exception as e:  # noqa: BLE001
            # A corpus holds files no bytecode compiler accepts -- inline
            # assembler, native decorators, deliberate syntax errors.  What
            # has to match between stages is *which* files those are and why,
            # so they are recorded rather than skipped silently.
            rejected.append("%s\t%s: %s"
                            % (basename(path), type(e).__name__, e))
            continue
        out = open(out_dir + "/" + mpy_name(path), "wb")
        out.write(data)
        out.close()
        ok += 1
    rejected.sort()
    man = open(out_dir + "/" + REJECTED, "w")
    man.write("\n".join(rejected))
    if rejected:
        man.write("\n")
    man.close()
    print("compiled %d files into %s (%d rejected)"
          % (ok, out_dir, len(rejected)))
    # Checked after the work, not before: some of the compiler's modules are
    # imported lazily inside compile_source, so before the first compile only
    # part of the package is loaded.
    if path_dir is not None and not verify_loaded_from(path_dir):
        return 1
    return 0


# -- the orchestrating role (host only) ----------------------------------


def read(path):
    with open(path, "rb") as f:
        return f.read()


def sources(mpyc_dir):
    out = []
    for name in sorted(os.listdir(mpyc_dir)):
        if name.endswith(".py"):
            out.append(os.path.abspath(os.path.join(mpyc_dir, name)))
    return out


def find_py(roots):
    out = []
    for root in roots:
        if os.path.isfile(root):
            out.append(os.path.abspath(root))
            continue
        for d, _dirs, files in os.walk(root):
            for f in sorted(files):
                if f.endswith(".py"):
                    out.append(os.path.abspath(os.path.join(d, f)))
    out.sort()
    return out


def compare_dirs(a, b, label):
    """Every ``.mpy`` in *a* must be byte-identical in *b*."""
    names = sorted(n for n in os.listdir(a) if n.endswith(".mpy"))
    other = sorted(n for n in os.listdir(b) if n.endswith(".mpy"))
    if names != other:
        print("FAIL %s: different file sets" % label)
        print("  only in %s: %r" % (a, sorted(set(names) - set(other))))
        print("  only in %s: %r" % (b, sorted(set(other) - set(names))))
        return 1
    bad = 0
    for name in names:
        if read(os.path.join(a, name)) != read(os.path.join(b, name)):
            print("FAIL %s: %s differs (%d vs %d bytes)"
                  % (label, name, len(read(os.path.join(a, name))),
                     len(read(os.path.join(b, name)))))
            bad += 1
    # The two stages must also refuse the same files, with the same reason.
    ra = os.path.join(a, REJECTED)
    rb = os.path.join(b, REJECTED)
    n_rejected = 0
    if os.path.exists(ra) and os.path.exists(rb):
        la = read(ra).decode("utf-8", "replace").splitlines()
        lb = read(rb).decode("utf-8", "replace").splitlines()
        n_rejected = len(la)
        if la != lb:
            only_a = [x for x in la if x not in lb]
            only_b = [x for x in lb if x not in la]
            print("FAIL %s: the stages refuse different files" % label)
            for x in (only_a + only_b)[:8]:
                print("    %s" % x)
            bad += 1
    print("%-34s %d files, %d rejected alike, %d differ"
          % (label, len(names), n_rejected, bad))
    return bad


def run_stage(micropython, heapsize, path_dir, out_dir, paths, here):
    """Compile *paths* with the mpyc that lives in *path_dir*."""
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    argv = [micropython]
    if heapsize:
        argv += ["-X", "heapsize=" + heapsize]
    argv += [os.path.join(here, "tools", "bootstrap.py"),
             "--path", path_dir, "--compile", out_dir] + paths
    # Run from elsewhere, so that the empty entry MicroPython puts on
    # sys.path cannot reach the checkout's own mpyc/.
    proc = subprocess.run(argv, capture_output=True, cwd=os.sep)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        print("FAIL: stage failed (%d)" % proc.returncode)
        print(out[-3000:])
        print(err[-3000:])
        return False
    sys.stdout.write(out)
    return True


def oracle_check(mpy_cross, paths, stage0):
    """Stage 0's output, against mpy-cross's, file by file."""
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp()
    ref_path = os.path.join(tmp, "ref.mpy")
    bad = 0
    for path in paths:
        proc = subprocess.run(
            [mpy_cross, "-o", ref_path, "-s", basename(path), path],
            capture_output=True)
        if proc.returncode != 0:
            print("FAIL: the oracle rejects %s" % path)
            bad += 1
            continue
        mine = read(os.path.join(stage0, mpy_name(path)))
        if mine != read(ref_path):
            print("FAIL: %s differs from the oracle (%d vs %d bytes)"
                  % (basename(path), len(mine), len(read(ref_path))))
            bad += 1
    print("%-34s %d files, %d differ" % ("stage 0 vs mpy-cross:", len(paths),
                                         bad))
    return bad


def main(argv):
    out_dir = None
    paths = []
    micropython = None
    mpy_cross = None
    heapsize = "256M"
    build = None
    corpus = []
    path_dir = None
    mode = None
    it = iter(argv)
    for a in it:
        if a == "--compile":
            mode = "compile"
            out_dir = next(it)
        elif a == "--path":
            path_dir = next(it)
        elif a == "--micropython":
            micropython = next(it)
        elif a == "--mpy-cross":
            mpy_cross = next(it)
        elif a == "--heapsize":
            heapsize = next(it)
        elif a == "--build":
            build = next(it)
        elif a == "--corpus":
            corpus.append(next(it))
        else:
            paths.append(a)

    if path_dir:
        sys.path.insert(0, path_dir)

    if mode == "compile":
        return compile_into(out_dir, paths, path_dir)

    # The orchestrating role needs the host's os.path and subprocess.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if micropython is None:
        print(__doc__)
        return 2
    if build is None:
        build = os.path.join(here, "build", "bootstrap")
    mpyc_dir = os.path.join(here, "mpyc")
    srcs = sources(mpyc_dir)
    print("sources: %d modules of the compiler itself" % len(srcs))

    stages = []
    for i in range(3):
        d = os.path.join(build, "stage%d" % i, "mpyc")
        os.makedirs(d, exist_ok=True)
        for name in os.listdir(d):
            if name.endswith(".mpy") or name == REJECTED:
                os.remove(os.path.join(d, name))
        stages.append(d)

    # Stage 0: the host's Python runs the compiler from source.
    sys.path.insert(0, here)
    compile_into(stages[0], srcs)
    if read(os.path.join(stages[0], REJECTED)):
        print("FAIL: the compiler refuses part of its own source")
        print(read(os.path.join(stages[0], REJECTED)).decode())
        return 1

    bad = 0
    if mpy_cross:
        bad += oracle_check(mpy_cross, srcs, stages[0])

    # Stages 1 and 2: the compiler runs as the bytecode the previous stage
    # emitted.  --path points at the parent of the mpyc package directory.
    for i in (1, 2):
        parent = os.path.dirname(stages[i - 1])
        if not run_stage(micropython, heapsize, parent, stages[i], srcs,
                         here):
            return 1
        bad += compare_dirs(stages[i - 1], stages[i],
                            "stage %d vs stage %d:" % (i, i - 1))

    if corpus:
        files = find_py(corpus)
        print("corpus : %d files" % len(files))
        c0 = os.path.join(build, "corpus0")
        c1 = os.path.join(build, "corpus1")
        for d in (c0, c1):
            os.makedirs(d, exist_ok=True)
            for name in os.listdir(d):
                if name.endswith(".mpy") or name == REJECTED:
                    os.remove(os.path.join(d, name))
        # Files sharing a basename would overwrite each other, so keep one of
        # each; the point is breadth of syntax, not of file names.
        seen = {}
        unique = []
        for p in files:
            b = basename(p)
            if b not in seen:
                seen[b] = p
                unique.append(p)
        compile_into(c0, unique)
        if not run_stage(micropython, heapsize, os.path.dirname(stages[0]),
                         c1, unique, here):
            return 1
        bad += compare_dirs(c0, c1, "corpus: stage 1 vs stage 0:")

    print("")
    if bad:
        print("summary: %d difference(s) -- not a fixed point" % bad)
        return 1
    print("summary: bootstrap reaches a fixed point")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
