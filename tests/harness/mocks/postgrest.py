"""Mock PostgREST: a strict URL -> canned response map plus a request spy.

Route map keys look like ``"GET /users?select=user_id,user_name"``; query
parameters are matched order-insensitively (duplicates preserved). Every
request is recorded and can be inspected through ``GET /__requests``;
``POST /__reset`` clears the log. Unmatched requests answer 500 and are
still recorded, so unexpected upstream calls fail loudly.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, Response


def canonical_key(method: str, path: str, query: str = "") -> str:
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    rendered = "&".join(f"{key}={value}" for key, value in pairs)
    return f"{method.upper()} {path}" + (f"?{rendered}" if rendered else "")


def key_from_spec(spec: str) -> str:
    method, _, remainder = spec.strip().partition(" ")
    path, _, query = remainder.partition("?")
    return canonical_key(method, path, query)


def index_routes(routes: dict[str, dict]) -> dict[str, dict]:
    return {key_from_spec(spec): response for spec, response in routes.items()}


def match_route(index: dict[str, dict], method: str, path: str,
                query: str = "") -> dict | None:
    return index.get(canonical_key(method, path, query))


def build_response(spec: dict) -> Response:
    status = int(spec.get("status", 200))
    headers = spec.get("headers") or {}
    if "json" in spec:
        return Response(content=json.dumps(spec["json"]), status_code=status,
                        media_type=spec.get("content_type", "application/json"),
                        headers=headers)
    if "text" in spec:
        return Response(content=spec["text"], status_code=status,
                        media_type=spec.get("content_type", "text/plain"),
                        headers=headers)
    return Response(status_code=status, headers=headers)


def create_app(*, routes: dict[str, dict]) -> FastAPI:
    app = FastAPI(title="mock-postgrest", docs_url=None, redoc_url=None,
                  openapi_url=None)
    index = index_routes(routes)
    spy: list[dict] = []

    def handle(method: str, path: str, request: Request) -> Response:
        spy.append({
            "method": method,
            "path": path,
            "query": request.url.query,
            "headers": dict(request.headers),
        })
        spec = match_route(index, method, path, request.url.query)
        if spec is None:
            url = f"{method} {path}" + (f"?{request.url.query}" if request.url.query else "")
            return Response(
                content=json.dumps({"error": f"mock postgrest: no route for {url}"}),
                status_code=500,
                media_type="application/json",
            )
        return build_response(spec)

    @app.get("/__requests")
    def requests() -> list[dict]:
        return list(reversed(spy))

    @app.post("/__reset")
    def reset() -> dict:
        spy.clear()
        return {"reset": True}

    @app.api_route("/{path:path}", methods=["GET"])
    def catch_all(path: str, request: Request) -> Response:
        return handle("GET", f"/{path}", request)

    @app.get("/")
    def root(request: Request) -> Response:
        return handle("GET", "/", request)

    return app
