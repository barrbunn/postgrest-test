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

import os
from urllib.parse import parse_qsl, urlencode

import httpx
import psycopg
from fastapi import FastAPI, Request, Response
from jose import JWTError, jwk as jose_jwk, jwt

app = FastAPI(title="pgrmapper", docs_url=None, openapi_url=None, redoc_url=None)


class AuthError(Exception):
    pass


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


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def load_access_filter() -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = {}
    try:
        with psycopg.connect(db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"{_q(mapper_schema())}.access_filter",))
                if cur.fetchone()[0] is None:
                    return result
                cur.execute(f"SELECT role, \"table\", visible_columns FROM {_q(mapper_schema())}.access_filter")
                for role, table, cols in cur.fetchall():
                    result[(role, table)] = list(cols)
    except psycopg.Error:
        pass
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
    url = idp_jwks_url()
    if not url:
        return []
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return [jose_jwk.construct(k) for k in resp.json().get("keys", [])]


def verify_user_jwt_jwks(token: str) -> dict | None:
    try:
        keys = _fetch_idp_keys()
    except (httpx.HTTPError, ValueError):
        keys = []
    for key in keys:
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


def split_top_level(select: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in select:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def transform_query(query: str, visible: list[str]) -> tuple[str, bool]:
    """Rewrite the query string's select= against the visible columns.

    Returns (new_query, blocked). blocked=True when the intersection of the
    requested columns with the visible ones is empty.
    """
    params = parse_qsl(query, keep_blank_values=True)
    select = None
    rest = []
    for key, value in params:
        if key.lower() == "select":
            select = value
        else:
            rest.append((key, value))

    if select is None or select.strip() in ("", "*"):
        new_select = ",".join(visible)
    else:
        kept: list[str] = []
        for part in split_top_level(select):
            if "(" in part:
                kept.append(part)  # embedded resource: pass through
                continue
            base = part.split("->")[0].split(":")[-1].strip()
            if base == "*":
                kept.extend(visible)
            elif base in visible:
                kept.append(part)
        deduped = []
        seen = set()
        for part in kept:
            if part not in seen:
                seen.add(part)
                deduped.append(part)
        if not deduped:
            return query, True
        new_select = ",".join(deduped)

    return urlencode([("select", new_select)] + rest), False


def _forward(request: Request, path: str, query_override: str | None = None) -> Response:
    query = request.url.query or "" if query_override is None else query_override
    target = f"{upstream()}{path}" + (f"?{query}" if query else "")
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in ("authorization", "accept", "content-type")
    }
    with httpx.Client() as client:
        upstream_resp = client.get(target, headers=headers)
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
            return Response(
                content=f'{{"error":"{ex}"}}',
                status_code=401,
                headers={"content-type": "application/json"},
            )
        visible = load_access_filter().get((role, table))
        if visible is not None:
            new_query, blocked = transform_query(request.url.query or "", visible)
            if blocked:
                return Response(
                    content=f'{{"error":"role {role} has no visible columns on {table}"}}',
                    status_code=403,
                    headers={"content-type": "application/json"},
                )
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
    with psycopg.connect(db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = %s AND table_name = %s AND table_type IN ('BASE TABLE', 'VIEW')",
                (db_schema(), table),
            )
            return cur.fetchone() is not None


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
