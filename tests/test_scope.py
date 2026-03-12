"""Unit tests for core/scope.py — path permission checking."""

from mut.core.scope import check_path_permission


def _scope(path="/src/", exclude=None, mode="rw"):
    return {"id": "test", "path": path, "exclude": exclude or [], "mode": mode, "agents": ["a"]}


def test_basic_read():
    assert check_path_permission(_scope(), "src/main.py", "read")


def test_outside_scope():
    assert not check_path_permission(_scope(), "docs/readme.md", "read")


def test_root_scope_allows_all():
    assert check_path_permission(_scope(path="/"), "anything/at/all.txt", "read")


def test_exclude():
    s = _scope(path="/src/", exclude=["/src/vendor/"])
    assert check_path_permission(s, "src/main.py", "read")
    assert not check_path_permission(s, "src/vendor/lib.py", "read")


def test_read_only_blocks_write():
    s = _scope(mode="r")
    assert check_path_permission(s, "src/main.py", "read")
    assert not check_path_permission(s, "src/main.py", "write")


def test_rw_allows_write():
    s = _scope(mode="rw")
    assert check_path_permission(s, "src/main.py", "write")


def test_scope_path_exact_match():
    assert check_path_permission(_scope(path="/src/"), "src", "read")
