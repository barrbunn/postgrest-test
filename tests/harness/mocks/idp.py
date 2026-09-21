"""Mock IdP: publishes the JWKS document for the session IdP key."""

from __future__ import annotations

from fastapi import FastAPI

from tests.harness.tokens import jwks


def create_app(*, public_pem: str, kid: str, realm: str, issuer: str) -> FastAPI:
    app = FastAPI(title="mock-idp", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/realms/{realm_name}/.well-known/openid-configuration")
    def discovery(realm_name: str) -> dict:
        base = issuer.rstrip("/")
        return {"issuer": base, "jwks_uri": f"{base}/protocol/openid-connect/certs"}

    @app.get("/realms/{realm_name}/protocol/openid-connect/certs")
    def certs(realm_name: str) -> dict:
        return jwks(public_pem, kid)

    return app
