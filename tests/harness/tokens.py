"""Ephemeral RSA keys and JWT minting for the harness.

Mirrors the production contract: the IdP key signs user JWTs, the gateway key
signs the service JWT that embeds the user JWT (see
`nginx/conf.d/apigee.js`). Keys live only for the test session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt


def generate_rsa_keypair(bits: int = 2048) -> tuple[str, str]:
    """(private_pem, public_pem) for a fresh RSA key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def jwks(public_pem: str, kid: str) -> dict:
    """JWKS document for a public key (what the mock IdP serves)."""
    key = jose_jwk.construct(public_pem, "RS256").to_dict()
    key.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [key]}


@dataclass
class Keys:
    """Session keys plus token helpers bound to an issuer/audience pair."""

    idp_private_pem: str
    idp_public_pem: str
    gateway_private_pem: str
    gateway_public_pem: str
    kid: str = "test-kid"
    audience: str = "myclient"
    idp_issuer: str = "http://mock-idp/realms/test"
    gateway_issuer: str = "apigee"
    paths: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def generate(cls, *, kid: str = "test-kid", audience: str = "myclient",
                 idp_issuer: str = "http://mock-idp/realms/test") -> "Keys":
        idp_private, idp_public = generate_rsa_keypair()
        gateway_private, gateway_public = generate_rsa_keypair()
        return cls(
            idp_private_pem=idp_private,
            idp_public_pem=idp_public,
            gateway_private_pem=gateway_private,
            gateway_public_pem=gateway_public,
            kid=kid,
            audience=audience,
            idp_issuer=idp_issuer,
        )

    def write(self, directory: Path) -> dict[str, Path]:
        """Write the PEMs to directory; returns the paths by logical name."""
        directory.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "idp_private": directory / "idp-private.pem",
            "idp_public": directory / "idp-public.pem",
            "gateway_private": directory / "gateway-jwt.key",
            "gateway_public": directory / "gateway-jwt-public.pem",
        }
        for path, content in (
            (self.paths["idp_private"], self.idp_private_pem),
            (self.paths["idp_public"], self.idp_public_pem),
            (self.paths["gateway_private"], self.gateway_private_pem),
            (self.paths["gateway_public"], self.gateway_public_pem),
        ):
            path.write_text(content)
        return self.paths

    def user_jwt(self, role: str, *, sub: str = "alice", roles: list[str] | None = None,
                 audience: str | None = None, issuer: str | None = None,
                 expires_in: int = 300) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer or self.idp_issuer,
            "aud": audience or self.audience,
            "sub": sub,
            "preferred_username": sub,
            "iat": now,
            "exp": now + expires_in,
            "realm_access": {"roles": roles or [role]},
        }
        return jwt.encode(claims, self.idp_private_pem, algorithm="RS256",
                          headers={"kid": self.kid})

    def service_jwt(self, user_jwt: str, role: str, *, sub: str = "",
                    expires_in: int = 60) -> str:
        now = int(time.time())
        claims = {
            "iss": self.gateway_issuer,
            "sub": sub,
            "role": role,
            "user_jwt": user_jwt,
            "exp": now + expires_in,
        }
        return jwt.encode(claims, self.gateway_private_pem, algorithm="RS256",
                          headers={"kid": self.kid})

    def gateway_headers(self, role: str, *, sub: str = "alice",
                        user_jwt: str | None = None) -> dict[str, str]:
        """The headers the real nginx gateway sets on the pgrmapper request."""
        token = user_jwt if user_jwt is not None else self.user_jwt(role, sub=sub)
        service = self.service_jwt(token, role, sub=sub)
        return {"Authorization": f"Bearer {service}", "X-User-Role": role}
