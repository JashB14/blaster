import argparse, ast, bisect, gzip, json
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--repo", default="/app/sqlglot")
ap.add_argument("--cov", default="/app/coverage.json")
ap.add_argument("--pool")
ap.add_argument("--log", default="/app/class_use.log")
ap.add_argument("--tests", default="sample/tests.json")
ap.add_argument("--out", default="maps")
a = ap.parse_args()
repo, out = Path(a.repo), Path(a.out)


def load_json(p):
    p = str(p)
    with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f:
        return json.load(f)


tests = load_json(a.tests)["tests"]
ids = sorted({t["id"] for t in tests})
idx = {i: n for n, i in enumerate(ids)}
by_file = defaultdict(set)
for t in tests:
    by_file[t["file"]].add(idx[t["id"]])

raw = defaultdict(lambda: defaultdict(set))


def add_cov(path, suffixes):
    for fp, fd in load_json(path)["files"].items():
        for line, ctxs in fd.get("contexts", {}).items():
            for c in ctxs:
                if suffixes:
                    s = next((s for s in suffixes if c.endswith(s)), None)
                    if not s:
                        continue
                    c = c[: -len(s)]
                if c in idx:
                    raw[fp][int(line)].add(idx[c])


add_cov(a.cov, ("|run", "|setup"))
if a.pool:
    add_cov(a.pool, ())          # pool contexts are plain test ids

use, fix = defaultdict(set), defaultdict(set)
if Path(a.log).exists():
    for ln in open(a.log):
        cur, _, key = ln.rstrip("\n").partition("\t")
        who = by_file.get(cur[5:], set()) if cur.startswith("FILE:") else ({idx[cur]} if cur in idx else set())
        if key.startswith("F:"):
            fix[key[2:]] |= who
        else:
            use[key] |= who

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.ExceptHandler)


def blocks(node):
    for f in ("body", "orelse", "finalbody", "handlers"):
        v = getattr(node, f, None)
        if v:
            yield v


def start_of(s):
    return min([s.lineno] + [d.lineno for d in getattr(s, "decorator_list", [])])


def build(rel, src):
    n = len(src.splitlines())
    r = raw.get(rel, {})
    cl = sorted(r)

    def span(x, y):
        s = set()
        for l in cl[bisect.bisect_left(cl, x): bisect.bisect_right(cl, y)]:
            s |= r[l]
        return s

    allset = span(1, n)
    for k, v in use.items():
        if k.startswith(rel + "::"):
            allset |= v
    eff, par, doc = [allset] * (n + 2), [0] * (n + 2), [0] * (n + 2)

    def put(x, y, s, p):
        for l in range(x, min(y, n) + 1):
            eff[l] = s
            par[l] = p if p < l else par[p]

    def visit(stmts, mode, hdr, cs, qual):
        for s in stmts:
            x, y = start_of(s), s.end_lineno
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
                for l in range(x, y + 1):
                    doc[l] = 1
            if isinstance(s, ast.ClassDef):
                q = f"{qual}.{s.name}" if qual else s.name
                own = span(x, y) | use.get(f"{rel}::{q}", set())
                put(x, y, own, 0)
                visit(s.body, "import", 0, own, q)
            elif isinstance(s, FUNCS):
                put(x, y, span(x, y), 0)
                visit(s.body, "run", 0, None, qual)
            elif mode == "import":
                put(x, y, cs, 0)               # raw coverage of import-time lines is unreliable
                for b in blocks(s):
                    visit(b, "import", 0, cs, qual)
            elif isinstance(s, COMPOUND):
                put(x, y, span(x, y), hdr)
                hend = max(x, (s.body[0].lineno if s.body else x + 1) - 1)
                put(x, hend, span(x, hend), hdr)
                for b in blocks(s):
                    visit(b, "run", x, None, qual)
            else:
                put(x, y, span(x, y), hdr)

    visit(ast.parse(src).body, "import", 0, allset, "")
    tbl, ix = [], {}

    def sid(s):
        k = tuple(sorted(s))
        if k not in ix:
            ix[k] = len(tbl)
            tbl.append(list(k))
        return ix[k]

    return {"sets": tbl, "eff": [sid(eff[l]) for l in range(1, n + 1)], "par": par[1: n + 1],
            "doc": [l for l in range(1, n + 1) if doc[l]], "all": sid(allset)}


(out / "shards").mkdir(parents=True, exist_ok=True)
for p in sorted((repo / "sqlglot").rglob("*.py")):
    rel = str(p.relative_to(repo))
    shard = build(rel, p.read_text())
    (out / "shards" / (rel.replace("/", "__") + ".json")).write_text(json.dumps(shard, separators=(",", ":")))
(out / "ids.json").write_text(json.dumps(ids))
(out / "fixtures.json").write_text(json.dumps({k: sorted(v) for k, v in fix.items()}))
print("done; size MB:", sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6)