"""
Safety net for moving dead code (the repo has no tests).

For every .py file (outside .venv/, workload_synergy/, graveyard/), runs only
its top-level import statements and sys.path tweaks in a fresh interpreter,
and records whether they load and which repo files they pull in. Module
bodies are never executed, so nothing gets launched.

usage:
  python repro_runs/check_imports.py --save   # record baseline
  python repro_runs/check_imports.py          # compare against baseline
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO, "repro_runs", "check_imports_baseline.json")
SKIP_DIRS = {".venv", "workload_synergy", "graveyard", "__pycache__", ".git"}

# Runs inside the child interpreter: exec the file's imports with the right
# __file__/__package__ so sys.path tweaks and relative imports behave.
CHILD = r"""
import ast, contextlib, io, json, os, sys
repo, path, package = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    tree = ast.parse(open(path).read())
except SyntaxError as e:
    print(json.dumps({"status": f"SyntaxError: line {e.lineno}", "loaded": []}))
    sys.exit()
tweaks = [n for n in tree.body if "sys.path" in ast.unparse(n)]
used = {x.id for n in tweaks for x in ast.walk(n) if isinstance(x, ast.Name)}
keep = [n for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom)) or n in tweaks
        # simple constants the sys.path lines use, e.g. REPO = os.path.dirname(...)
        or (isinstance(n, ast.Assign) and {t.id for t in n.targets if isinstance(t, ast.Name)} & used)]
code = compile(ast.Module(body=ast.parse("import sys, os").body + keep, type_ignores=[]), path, "exec")
if not package:
    sys.path.insert(0, os.path.dirname(path))  # as `python path/to/script.py` does
status = "OK"
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        exec(code, {"__file__": path, "__name__": "_check", "__package__": package or None})
except BaseException as e:
    status = f"{type(e).__name__}: {e}"
loaded = sorted({os.path.relpath(m.__file__, repo) for m in list(sys.modules.values())
                 if getattr(m, "__file__", None) and m.__file__.startswith(repo + os.sep)
                 and "/.venv/" not in m.__file__ and os.path.abspath(m.__file__) != path})
print(json.dumps({"status": status, "loaded": loaded}))
"""


def py_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(root, f), REPO)


def package_of(rel):
    parts = rel.split(os.sep)[:-1]
    # longest chain of parent dirs that all have __init__.py
    pkg = []
    for i in range(len(parts)):
        if os.path.exists(os.path.join(REPO, *parts[: i + 1], "__init__.py")):
            pkg.append(parts[i])
        else:
            pkg = []
    return ".".join(pkg)


def check(rel):
    env = dict(os.environ, PYTHONPATH=REPO, PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION="python",
               PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run(
        [sys.executable, "-c", CHILD, REPO, os.path.join(REPO, rel), package_of(rel)],
        cwd=REPO, env=env, capture_output=True, text=True, check=False,
    )
    try:
        return rel, json.loads(out.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return rel, {"status": "CHECKER CRASHED: " + out.stderr.strip()[-200:], "loaded": []}


def main():
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(check, py_files()))
    if "--save" in sys.argv:
        with open(BASELINE, "w") as f:
            json.dump(results, f, indent=1, sort_keys=True)
        ok = sum(r["status"] == "OK" for r in results.values())
        print(f"saved baseline: {len(results)} files, {ok} import OK, {len(results) - ok} fail")
        return

    with open(BASELINE) as f:
        base = json.load(f)
    problems = 0
    for rel in sorted(set(base) | set(results)):
        if rel not in results:
            where = "moved to graveyard/" if os.path.exists(os.path.join(REPO, "graveyard", rel)) else "MISSING"
            print(f"{where:20s} {rel}")
            problems += where == "MISSING"
            continue
        if rel not in base:
            print(f"{'new file':20s} {rel}  ({results[rel]['status']})")
            continue
        b, r = base[rel], results[rel]
        if b["status"] != r["status"]:
            print(f"{'STATUS CHANGED':20s} {rel}\n{'':22s}before: {b['status']}\n{'':22s}after:  {r['status']}")
            problems += 1
        lost = sorted(set(b["loaded"]) - set(r["loaded"]))
        if lost:
            print(f"{'NO LONGER LOADS':20s} {rel}: {', '.join(lost)}")
            problems += 1
    print(f"\n{len(results)} files checked; {problems} problem(s) vs baseline")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
