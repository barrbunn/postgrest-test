"""pgrmapper: read-only filtering proxy for PostgREST.

GET routes are validated against the live database schema per request (so
schemas applied after startup are served without a restart). Two identity
modes are supported:

- Gateway mode (Apigee mimic): the request carries an `X-User-Role` header
  and a service JWT (RS256, signed by the gateway). The service JWT is
  verified with the gateway public key and the user JWT embedded in it is
  verified against the IdP JWKS endpoint. The header role drives the
  projection rules.
- Legacy mode (driver debugging): a user JWT signed with PGRST_JWT_SECRET
  (HS256), role taken from the JWT `role` claim; anonymous when no token.
"""

import json
import os
import re
import time
from urllib.parse import parse_qsl, urlencode

import httpx
import psycopg
from fastapi import FastAPI, Request, Response
from jose import JWTError, jwk as jose_jwk, jwt
from psycopg_pool import ConnectionPool

app = FastAPI(title="pgrmapper", docs_url=None, openapi_url=None, redoc_url=None)


class AuthError(Exception):
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def db_pool_min() -> int:
    return int(_env("PGMAPPER_DB_POOL_MIN", "1"))


def db_pool_max() -> int:
    return int(_env("PGMAPPER_DB_POOL_MAX", "10"))


def jwks_ttl() -> float:
    return float(_env("PGMAPPER_JWKS_TTL", "60"))


def filter_cache_ttl() -> float:
    return float(_env("PGMAPPER_CACHE_TTL", "5"))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def upstream() -> str:
    return _env("PGMAPPER_UPSTREAM", os.environ.get("POSTGREST_URL", "http://postgrest:3000")).rstrip("/")


def db_url() -> str:
    return _env(
        "PGMAPPER_DB_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql://{user}:{password}@{host}:{port}/{db}".format(
                user=_env("POSTGRES_USER", "app"),
                password=_env("POSTGRES_PASSWORD", "app_password"),
                host=_env("POSTGRES_HOST", "postgres"),
                port=_env("POSTGRES_PORT", "5432"),
                db=_env("POSTGRES_DB", "app"),
            ),
        ),
    )


def db_schema() -> str:
    return _env("PGMAPPER_DB_SCHEMA", "public")


def mapper_schema() -> str:
    return _env("PGMAPPER_SCHEMA", "pgrmapper")


def anon_role() -> str:
    return _env("PGMAPPER_ANON_ROLE", "anon")


QUERY_POLICIES = ("allow", "enforce", "reject")


def query_policy() -> str:
    """How requests that reference non-visible columns are handled.

    allow   - forward them (projection is still rewritten)
    enforce - strip the offending columns/conditions from the request
    reject  - answer 403 without forwarding

    Unknown values fall back to reject.
    """
    policy = _env("PGMAPPER_QUERY_POLICY", "reject").strip().lower()
    return policy if policy in QUERY_POLICIES else "reject"


def _error(status: int, message: str) -> Response:
    return Response(
        content=json.dumps({"error": message}),
        status_code=status,
        headers={"content-type": "application/json"},
    )


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


# --- shared, lazily-created resources (thread-safe) ---------------------------

_db_pool: ConnectionPool | None = None


def db_conn():
    """Pooled database connection (psycopg_pool), sized via env vars."""
    global _db_pool
    if _db_pool is None:
        _db_pool = ConnectionPool(
            db_url(), min_size=db_pool_min(), max_size=db_pool_max(), open=True
        )
    return _db_pool.connection()


_http: httpx.Client | None = None


def http_client() -> httpx.Client:
    """One shared httpx client for the app lifetime: keepalive connection
    pooling to the upstream services instead of a fresh client (and fresh
    TCP/TLS handshake) per request."""
    global _http
    if _http is None:
        _http = httpx.Client(timeout=10.0)
    return _http


_jwks_cache: dict = {"keys": None, "ts": 0.0}
_filter_cache: dict = {"rules": None, "ts": 0.0}


def load_access_filter() -> dict[tuple[str, str], list[str]]:
    """(role, table) -> visible_columns, cached locally for a short TTL."""
    now = time.monotonic()
    if _filter_cache["rules"] is not None and now - _filter_cache["ts"] < filter_cache_ttl():
        return _filter_cache["rules"]
    result: dict[tuple[str, str], list[str]] = {}
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"{_q(mapper_schema())}.access_filter",))
                if cur.fetchone()[0] is not None:
                    cur.execute(f"SELECT role, \"table\", visible_columns FROM {_q(mapper_schema())}.access_filter")
                    for role, table, cols in cur.fetchall():
                        result[(role, table)] = list(cols)
    except psycopg.Error:
        pass
    _filter_cache["rules"] = result
    _filter_cache["ts"] = now
    return result


def gateway_public_key() -> str | None:
    return os.environ.get("PGMAPPER_GATEWAY_JWT_PUBLIC_KEY")


def idp_jwks_url() -> str | None:
    return os.environ.get("PGMAPPER_IDP_JWKS_URL")


def idp_audience() -> str:
    return os.environ.get("PGMAPPER_IDP_AUDIENCE", "myclient")


def verify_service_jwt(token: str) -> dict | None:
    key_path = gateway_public_key()
    if not key_path:
        return None
    try:
        with open(key_path) as f:
            key = f.read()
        return jwt.decode(token, key, algorithms=["RS256"])
    except (JWTError, OSError):
        return None


def _fetch_idp_keys() -> list:
    """IdP public keys, cached locally for PGMAPPER_JWKS_TTL seconds."""
    now = time.monotonic()
    if _jwks_cache["keys"] is not None and now - _jwks_cache["ts"] < jwks_ttl():
        return _jwks_cache["keys"]
    url = idp_jwks_url()
    if not url:
        keys = []
    else:
        try:
            resp = http_client().get(url)
            resp.raise_for_status()
            keys = [jose_jwk.construct(k) for k in resp.json().get("keys", [])]
        except (httpx.HTTPError, ValueError):
            keys = []
    _jwks_cache["keys"] = keys
    _jwks_cache["ts"] = now
    return keys


def verify_user_jwt_jwks(token: str) -> dict | None:
    for key in _fetch_idp_keys():
        try:
            return jwt.decode(token, key, algorithms=["RS256"], audience=idp_audience())
        except JWTError:
            continue
    return None


def resolve_role(request: Request) -> str:
    """Resolve the role for projections.

    Gateway mode when the X-User-Role header is present (Apigee mimic):
    verify the service JWT and the embedded user JWT, then trust the header.
    Otherwise the legacy path: HS256 user JWT with PGRST_JWT_SECRET, or the
    anonymous role.
    """
    auth = request.headers.get("authorization", "")
    role_header = request.headers.get("x-user-role")
    if role_header is not None:
        if not auth.lower().startswith("bearer "):
            raise AuthError("missing service JWT")
        token = auth.split(" ", 1)[1].strip()
        claims = verify_service_jwt(token)
        if claims is None:
            raise AuthError("invalid service JWT")
        inner = claims.get("user_jwt")
        if not inner or verify_user_jwt_jwks(inner) is None:
            raise AuthError("invalid embedded user JWT")
        return role_header

    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        secret = os.environ.get("PGRST_JWT_SECRET")
        try:
            if secret:
                claims = jwt.decode(token, secret, algorithms=["HS256"])
            else:
                claims = jwt.get_unverified_claims(token)
            return claims.get("role") or anon_role()
        except JWTError:
            return anon_role()
    return anon_role()


def split_top_level(value: str) -> list[str]:
    """Split on commas that are not nested in (), [] or {}, nor quoted."""
    parts: list[str] = []
    depth = 0
    quoted = False
    current: list[str] = []
    for ch in value:
        if ch == '"':
            quoted = not quoted
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _is_embed(part: str) -> bool:
    return "(" in part and "::" not in part


def _column_base(ref: str) -> str:
    base = ref.split("->")[0]
    base = base.split("::")[0]
    return base.split(":")[-1].strip()


def _column_error(base: str, resource: str, role: str) -> str:
    prefix = f"{resource}." if resource else ""
    return f"column {prefix}{base} is not visible for role {role}"


def _split_embed(part: str) -> tuple[str, str]:
    name, _, inner = part.partition("(")
    inner = inner.strip()
    if inner.endswith(")"):
        inner = inner[:-1]
    resource = name.split(":")[-1].split("!")[0].strip().lstrip(".")
    return resource, inner


def _embed_aliases(select: str | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not select:
        return aliases
    for part in split_top_level(select):
        if not _is_embed(part):
            continue
        name = part.split("(", 1)[0].strip().lstrip(".")
        if ":" in name:
            alias, _, resource = name.rpartition(":")
            resource = resource.split("!")[0].strip()
            if alias.strip() and resource:
                aliases[alias.strip()] = resource
    return aliases


def _transform_select(select: str | None, role: str, rules: dict, policy: str,
                      visible: list[str], resource: str,
                      is_embed: bool = False) -> tuple[str | None, str | None]:
    """Rewrite a select expression against the visible columns.

    Returns (new_select, error). new_select=None drops an embedded resource;
    error is set when the request must be rejected (reject policy, or nothing
    visible left at the top level).
    """
    if select is None or select.strip() in ("", "*"):
        return ",".join(visible), None
    kept: list[str] = []
    for part in split_top_level(select):
        if _is_embed(part):
            if policy == "allow":
                kept.append(part)
                continue
            new_part, error = _transform_embed(part, role, rules, policy)
            if error:
                return None, error
            if new_part is not None:
                kept.append(new_part)
            continue
        base = _column_base(part)
        if base == "*":
            kept.extend(visible)
        elif base in visible:
            kept.append(part)
        elif policy == "reject":
            return None, _column_error(base, resource, role)
    deduped: list[str] = []
    seen: set = set()
    for part in kept:
        if part not in seen:
            seen.add(part)
            deduped.append(part)
    if not deduped:
        if is_embed:
            return None, None
        return None, f"role {role} has no visible columns on {resource}"
    return ",".join(deduped), None


def _transform_embed(part: str, role: str, rules: dict,
                     policy: str) -> tuple[str | None, str | None]:
    resource, inner = _split_embed(part)
    visible = rules.get((role, resource))
    if visible is None:
        return part, None
    if not visible:
        if policy == "reject":
            return None, f"role {role} has no visible columns on {resource}"
        return None, None
    new_inner, error = _transform_select(inner, role, rules, policy, visible,
                                         resource, is_embed=True)
    if error:
        return None, error
    if new_inner is None:
        return None, None
    return f"{part.split('(', 1)[0]}({new_inner})", None


def _check_column(ref: str, visible: list[str], resource: str, role: str,
                  policy: str) -> tuple[bool, str | None]:
    base = _column_base(ref)
    if base in visible:
        return True, None
    if policy == "reject":
        return False, _column_error(base, resource, role)
    return False, None


def _transform_condition(cond: str, visible: list[str], resource: str, role: str,
                         policy: str) -> tuple[str | None, str | None]:
    s = cond.strip()
    match = re.match(r"^(not\.)?(and|or)\s*\((.*)\)$", s, re.DOTALL)
    if match:
        prefix = "not." if match.group(1) else ""
        new_inner, error = _transform_expr(match.group(3), visible, resource, role, policy)
        if error:
            return None, error
        if new_inner is None:
            return None, None
        return f"{prefix}{match.group(2)}({new_inner})", None
    base = _column_base(s.split(".")[0])
    if base in visible:
        return s, None
    if policy == "reject":
        return None, _column_error(base, resource, role)
    return None, None


def _transform_expr(expr: str, visible: list[str], resource: str, role: str,
                    policy: str) -> tuple[str | None, str | None]:
    kept: list[str] = []
    for cond in split_top_level(expr):
        new_cond, error = _transform_condition(cond, visible, resource, role, policy)
        if error:
            return None, error
        if new_cond is not None:
            kept.append(new_cond)
    if not kept:
        return None, None
    return ",".join(kept), None


def _transform_tree(value: str, visible: list[str], resource: str, role: str,
                    policy: str) -> tuple[str | None, str | None]:
    inner = value.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    new_inner, error = _transform_expr(inner, visible, resource, role, policy)
    if error:
        return None, error
    if new_inner is None:
        return None, None
    return f"({new_inner})", None


def _transform_order_embed(item: str, role: str, rules: dict, aliases: dict,
                           policy: str) -> tuple[str | None, str | None]:
    name, _, remainder = item.partition("(")
    inner, close, suffix = remainder.rpartition(")")
    raw = name.split(":")[-1].split("!")[0].strip().lstrip(".")
    resource = aliases.get(raw, raw)
    visible = rules.get((role, resource))
    if visible is None:
        return item, None
    if not visible:
        if policy == "reject":
            return None, f"role {role} has no visible columns on {resource}"
        return None, None
    new_inner, error = _transform_order(inner, visible, resource, role, rules,
                                        aliases, policy)
    if error:
        return None, error
    if new_inner is None:
        return None, None
    return f"{name}({new_inner}){suffix if close else ''}", None


def _transform_order(value: str, visible: list[str], resource: str, role: str,
                     rules: dict, aliases: dict,
                     policy: str) -> tuple[str | None, str | None]:
    kept: list[str] = []
    for item in split_top_level(value):
        s = item.strip()
        if not s:
            continue
        if _is_embed(s):
            new_item, error = _transform_order_embed(s, role, rules, aliases, policy)
            if error:
                return None, error
            if new_item is not None:
                kept.append(new_item)
            continue
        base = _column_base(s.split(".")[0])
        if base in visible:
            kept.append(s)
        elif policy == "reject":
            return None, _column_error(base, resource, role)
    if not kept:
        return None, None
    return ",".join(kept), None


def _transform_param(key: str, value: str, role: str, rules: dict, aliases: dict,
                     table: str, visible: list[str],
                     policy: str) -> tuple[tuple[str, str] | None, str | None]:
    if key in ("limit", "offset"):
        return (key, value), None
    resource: str | None = None
    rest = key
    if key not in ("and", "or", "not.and", "not.or") and "." in key:
        raw, _, rest = key.partition(".")
        raw = raw.split("!")[0].strip()
        resource = aliases.get(raw, raw)

    if rest in ("and", "or", "not.and", "not.or"):
        if resource is None:
            target_visible, label = visible, table
        else:
            target_visible, label = rules.get((role, resource)), resource
            if target_visible is None:
                return (key, value), None
        if not target_visible:
            return None, f"role {role} has no visible columns on {label}"
        new_value, error = _transform_tree(value, target_visible, label, role, policy)
        if error:
            return None, error
        if new_value is None:
            return None, None
        return (key, new_value), None

    if resource is None:
        target_visible, label = visible, table
    else:
        target_visible, label = rules.get((role, resource)), resource
        if target_visible is None:
            return (key, value), None
        if not target_visible:
            return None, f"role {role} has no visible columns on {label}"

    if rest in ("limit", "offset"):
        return (key, value), None
    if rest == "order":
        new_value, error = _transform_order(value, target_visible, label, role,
                                            rules, aliases, policy)
        if error:
            return None, error
        if new_value is None:
            return None, None
        return (key, new_value), None

    allowed, error = _check_column(rest, target_visible, label, role, policy)
    if error:
        return None, error
    return ((key, value), None) if allowed else (None, None)


def transform_query(query: str, table: str, role: str,
                    rules: dict) -> tuple[str, str | None]:
    """Apply PGMAPPER_QUERY_POLICY to a query string.

    The select projection is always rewritten against the visible columns.
    Other parameters (filters, order, logic trees, embedded-resource
    references) are handled per policy: forwarded (allow), stripped (enforce)
    or rejected with an error message (reject).

    Returns (new_query, error).
    """
    policy = query_policy()
    visible = rules.get((role, table)) or []
    if not visible:
        return query, f"role {role} has no visible columns on {table}"
    select = None
    rest: list[tuple[str, str]] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.lower() == "select":
            select = value
        else:
            rest.append((key, value))

    aliases = _embed_aliases(select)
    new_select, error = _transform_select(select, role, rules, policy, visible, table)
    if error:
        return query, error

    kept_rest: list[tuple[str, str]] = []
    if policy != "allow":
        for key, value in rest:
            pair, error = _transform_param(key, value, role, rules, aliases, table,
                                           visible, policy)
            if error:
                return query, error
            if pair is not None:
                kept_rest.append(pair)
    else:
        kept_rest = rest

    return urlencode([("select", new_select)] + kept_rest), None


def _forward(request: Request, path: str, query_override: str | None = None) -> Response:
    query = request.url.query or "" if query_override is None else query_override
    target = f"{upstream()}{path}" + (f"?{query}" if query else "")
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in ("authorization", "accept", "content-type")
    }
    upstream_resp = http_client().get(target, headers=headers)
    content_type = upstream_resp.headers.get("content-type", "application/json")
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers={"content-type": content_type},
    )


def make_handler(table: str):
    def handler(request: Request) -> Response:
        try:
            role = resolve_role(request)
        except AuthError as ex:
            return _error(401, str(ex))
        rules = load_access_filter()
        visible = rules.get((role, table))
        if visible is not None:
            if not visible:
                return _error(403, f"role {role} has no visible columns on {table}")
            new_query, error = transform_query(request.url.query or "", table, role, rules)
            if error:
                return _error(403, error)
            return _forward(request, f"/{table}", query_override=new_query)
        return _forward(request, f"/{table}")

    return handler


def _root(request: Request) -> Response:
    try:
        resolve_role(request)
    except AuthError as ex:
        return Response(
            content=f'{{"error":"{ex}"}}',
            status_code=401,
            headers={"content-type": "application/json"},
        )
    return _forward(request, "/")


def table_exists(table: str) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = %s AND table_name = %s AND table_type IN ('BASE TABLE', 'VIEW')",
                (db_schema(), table),
            )
            return cur.fetchone() is not None


@app.on_event("shutdown")
def _close_shared() -> None:
    if _db_pool is not None:
        _db_pool.close()
    if _http is not None:
        _http.close()


@app.get("/health")
def health() -> dict:
    """Internal health endpoint (used by the container healthcheck).

    The gateway explicitly blocks /health, so it is not reachable from the
    host.
    """
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET"])
def catch_all(path: str, request: Request) -> Response:
    """Dynamic GET routing: tables are validated against the live schema, so
    schemas applied after startup are served without a restart."""
    if not path:
        return _root(request)
    table = path.split("/")[0]
    if not table_exists(table):
        return Response(
            content=f'{{"error":"no such resource: {table}"}}',
            status_code=404,
            headers={"content-type": "application/json"},
        )
    return make_handler(table)(request)


@app.on_event("startup")
def register_routes() -> None:
    app.add_api_route("/", _root, methods=["GET"])
