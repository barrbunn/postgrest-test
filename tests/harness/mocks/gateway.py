"""Mock nginx gateway: verifies the user JWT and mints the service JWT.

Mirrors `nginx/conf.d/apigee.js`: VerifyJWT (RS256 against the IdP key,
exp/iss/aud), role from `realm_access.roles[0]`, GenerateJWT (RS256 service
JWT embedding the user JWT) and forward with `X-User-Role`. Failures return
the same 401 JSON bodies as the njs handler.
"""

from __future__ import annotations

import time

import httpx
from fastapi import FastAPI, Request, Response
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError


def build_service_claims(user_jwt: str, role: str, *, sub: str = "",
                         issuer: str = "apigee", ttl: int = 60) -> dict:
    now = int(time.time())
    return {
        "iss": issuer,
        "sub": sub,
        "role": role,
        "user_jwt": user_jwt,
        "exp": now + ttl,
    }


def create_app(*, idp_public_pem: str, gateway_private_pem: str,
               pgrmapper_url: str, issuer: str, audience: str,
               service_issuer: str = "apigee", service_ttl: int = 60) -> FastAPI:
    app = FastAPI(title="mock-gateway", docs_url=None, redoc_url=None,
                  openapi_url=None)
    client = httpx.AsyncClient(timeout=10.0)

    def reject(status: int, message: str) -> Response:
        return Response(content=f'{{"error": "{message}"}}', status_code=status,
                        media_type="application/json")

    async def proxy(path: str, request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return reject(401, "missing user JWT")
        token = auth.split(" ", 1)[1].strip()
        try:
            claims = jwt.decode(token, idp_public_pem, algorithms=["RS256"],
                                options={"verify_aud": False})
        except ExpiredSignatureError:
            return reject(401, "user JWT expired")
        except JWTError:
            return reject(401, "invalid user JWT")
        if claims.get("iss") != issuer:
            return reject(401, "unexpected issuer")
        if claims.get("aud") != audience:
            return reject(401, "unexpected audience")

        roles = (claims.get("realm_access") or {}).get("roles") or []
        role = roles[0] if roles else "anon"
        service = jwt.encode(
            build_service_claims(token, role, sub=claims.get("sub", ""),
                                 issuer=service_issuer, ttl=service_ttl),
            gateway_private_pem,
            algorithm="RS256",
        )
        target = f"{pgrmapper_url.rstrip('/')}/{path}"
        if request.url.query:
            target += f"?{request.url.query}"
        forward_headers = {
            "Authorization": f"Bearer {service}",
            "X-User-Role": role,
        }
        for name in ("accept", "content-type"):
            value = request.headers.get(name)
            if value:
                forward_headers[name] = value
        upstream = await client.get(target, headers=forward_headers)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    @app.api_route("/{path:path}", methods=["GET"])
    async def catch_all(path: str, request: Request) -> Response:
        return await proxy(path, request)

    @app.get("/")
    async def root(request: Request) -> Response:
        return await proxy("", request)

    return app
