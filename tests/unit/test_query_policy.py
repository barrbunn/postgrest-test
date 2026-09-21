"""Policy matrix for pgrmapper's query rewriting (pure, in-process)."""

from __future__ import annotations

from urllib.parse import parse_qsl

import pytest

from pgrmapper.app import split_top_level, transform_query

RULES = {
    ("editor", "users"): ["user_id", "user_name", "user_email"],
    ("editor", "grants"): ["grant_id", "user_id", "application_id", "role_id",
                           "granted_at"],
    ("editor", "docs"): ["id", "data"],
    ("viewer", "grants"): [],
}


def _transform(monkeypatch, policy, table, query, role="editor"):
    monkeypatch.setenv("PGMAPPER_QUERY_POLICY", policy)
    return transform_query(query, table, role, RULES)


def _params(query: str) -> list[tuple[str, str]]:
    return parse_qsl(query, keep_blank_values=True)


ALLOW_CASES = [
    ("select=user_id,status&status=eq.disabled&limit=2",
     "select=user_id&status=eq.disabled&limit=2"),
    ("select=grant_id,users(status)", "select=grant_id,users(status)"),
    ("select=user_id&order=status.desc", "select=user_id&order=status.desc"),
]


@pytest.mark.parametrize("query,expected", ALLOW_CASES,
                         ids=lambda value: value if isinstance(value, str) else "")
def test_allow_forwards_parameters(monkeypatch, query, expected):
    table = "grants" if "grant" in query else "users"
    got, error = _transform(monkeypatch, "allow", table, query)
    assert error is None
    assert _params(got) == _params(expected)


REJECT_ERROR_CASES = [
    ("users", "select=user_id&status=eq.disabled", "users.status"),
    ("users", "select=user_id,status", "users.status"),
    ("users", "select=user_id&order=status.desc", "users.status"),
    ("users", "select=user_id&or=(status.eq.active,user_id.gt.5)", "users.status"),
    ("users", "select=user_id&not.and=(status.eq.active)", "users.status"),
    ("users", "select=user_id&status=not.eq.active", "users.status"),
    ("users", "select=user_id&nope=eq.1", "users.nope"),
    ("grants", "select=grant_id,users(status)", "users.status"),
    ("grants", "select=grant_id&users.status=eq.active", "users.status"),
    ("grants", "select=grant_id&users.order=status.desc", "users.status"),
    ("grants", "select=grant_id,author:users(*)&author.status=eq.active",
     "users.status"),
    ("grants", "select=grant_id,author:users(status)", "users.status"),
    ("docs", "select=id&secret->>x=eq.1", "docs.secret"),
    ("users", "select=user_id&or=(user_id.gt.5,status.eq.active,user_name.eq.x)",
     "users.status"),
]


@pytest.mark.parametrize("table,query,error_fragment", REJECT_ERROR_CASES,
                         ids=[case[1] for case in REJECT_ERROR_CASES])
def test_reject_refuses_inaccessible_columns(monkeypatch, table, query, error_fragment):
    got, error = _transform(monkeypatch, "reject", table, query)
    assert error is not None, f"expected a rejection for {query!r}, got {got!r}"
    assert error_fragment in error


REJECT_OK_CASES = [
    ("users", "select=user_id,user_name&limit=5&offset=10",
     "select=user_id,user_name&limit=5&offset=10"),
    ("users", 'select=user_id&or=(user_name.eq."Doe, John",user_id.gt.5)',
     'select=user_id&or=(user_name.eq."Doe, John",user_id.gt.5)'),
    ("grants", "select=grant_id,users(*)",
     "select=grant_id,users(user_id,user_name,user_email)"),
    ("docs", "select=id&data->>blood_type=eq.A-",
     "select=id&data->>blood_type=eq.A-"),
]


@pytest.mark.parametrize("table,query,expected", REJECT_OK_CASES,
                         ids=[case[1] for case in REJECT_OK_CASES])
def test_reject_forwards_legitimate_requests(monkeypatch, table, query, expected):
    got, error = _transform(monkeypatch, "reject", table, query)
    assert error is None
    assert _params(got) == _params(expected)


ENFORCE_CASES = [
    ("users", "select=user_id,status&status=eq.disabled&order=status.desc&limit=3",
     "select=user_id&limit=3"),
    ("grants", "select=grant_id,users(status)&limit=2", "select=grant_id&limit=2"),
    ("users", "select=user_id&or=(user_id.gt.5,status.eq.active)&limit=10",
     "select=user_id&or=(user_id.gt.5)&limit=10"),
    ("users", "select=user_id&not.and=(status.eq.active,user_id.gt.5)",
     "select=user_id&not.and=(user_id.gt.5)"),
    ("grants", "select=grant_id,author:users(*)&author.status=eq.active",
     "select=grant_id,author:users(user_id,user_name,user_email)"),
    ("users", "select=user_id&order=status.desc,user_name.asc",
     "select=user_id&order=user_name.asc"),
    ("users", "select=user_id&nope=eq.1", "select=user_id"),
    ("docs", "select=id&data->>blood_type=eq.A-",
     "select=id&data->>blood_type=eq.A-"),
]


@pytest.mark.parametrize("table,query,expected", ENFORCE_CASES,
                         ids=[case[1] for case in ENFORCE_CASES])
def test_enforce_strips_inaccessible_parts(monkeypatch, table, query, expected):
    got, error = _transform(monkeypatch, "enforce", table, query)
    assert error is None
    assert _params(got) == _params(expected)


def test_empty_visible_rule_blocks(monkeypatch):
    _, error = _transform(monkeypatch, "reject", "grants", "select=grant_id",
                          role="viewer")
    assert error == "role viewer has no visible columns on grants"


def test_unknown_policy_falls_back_to_reject(monkeypatch):
    _, error = _transform(monkeypatch, "bogus", "users",
                          "select=user_id&status=eq.disabled")
    assert error is not None
    assert "users.status" in error


SPLIT_CASES = [
    ("a,b,c", ["a", "b", "c"]),
    ("users(a,b),c", ["users(a,b)", "c"]),
    ('x.eq."Doe, John",y', ['x.eq."Doe, John"', "y"]),
    ("tags.cs.{a,b},id", ["tags.cs.{a,b}", "id"]),
    ("range.ov.[1,2],x", ["range.ov.[1,2]", "x"]),
    ("a(b(c,d),e),f", ["a(b(c,d),e)", "f"]),
]


@pytest.mark.parametrize("value,expected", SPLIT_CASES,
                         ids=[case[0] for case in SPLIT_CASES])
def test_split_top_level(value, expected):
    assert split_top_level(value) == expected
