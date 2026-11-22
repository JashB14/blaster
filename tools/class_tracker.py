import builtins, io, os, sys
import pytest

ROOT = os.environ.get("REPO", "/app/sqlglot")
LOG = os.environ.get("CLASS_LOG", "/app/class_use.log")
_cur, _seen, _fd = [None], set(), [None]


def _write(text):
    if _fd[0] is None:
        _fd[0] = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.write(_fd[0], text.encode())


def _record(obj):
    cur = _cur[0]
    t = type(obj)
    if cur is None or (cur, t) in _seen:
        return
    _seen.add((cur, t))
    out = []
    for c in t.__mro__:
        f = getattr(sys.modules.get(c.__module__), "__file__", None)
        if f and f.startswith(ROOT + "/sqlglot/"):
            out.append(f"{cur}\t{os.path.relpath(f, ROOT)}::{c.__qualname__}\n")
    _write("".join(out))


def _wrap(cls):
    orig = cls.__init__

    def __init__(self, *a, **k):
        _record(self)
        orig(self, *a, **k)

    cls.__init__ = __init__


_open = builtins.open


def _open_hook(file, *a, **k):
    try:
        p = os.path.abspath(os.fspath(file))
        cur = _cur[0]
        if cur and "/tests/fixtures/" in p and (cur, p) not in _seen:
            _seen.add((cur, p))
            _write(f"{cur}\tF:{os.path.relpath(p, ROOT)}\n")
    except Exception:
        pass
    return _open(file, *a, **k)


builtins.open = io.open = _open_hook


def pytest_configure(config):
    import sqlglot.expressions as e, sqlglot.generator as g, sqlglot.parser as p, sqlglot.tokens as t
    from sqlglot.dialects.dialect import Dialect
    for c in (e.Expression, p.Parser, g.Generator, t.Tokenizer, Dialect):
        _wrap(c)


def pytest_collectstart(collector):
    _cur[0] = "FILE:" + collector.nodeid   # collection-time use counts for the whole test file


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _cur[0] = item.nodeid
    yield
    _cur[0] = None