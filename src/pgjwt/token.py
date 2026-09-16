"""Token encoding shared by pgjwt and pgmkcurl."""

import os
import re
from datetime import datetime, timedelta, timezone

from jose import jwt


def parse_duration(text: str) -> int:
    match = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
    if not match:
        raise ValueError(f"invalid duration: {text!r} (examples: 1h, 30m, 2d)")
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


def encode(role: str, exp: str | None = None, claims: dict | None = None) -> str:
    secret = os.environ.get("PGRST_JWT_SECRET")
    if not secret:
        raise RuntimeError("PGRST_JWT_SECRET is not set")
    payload: dict = {"role": role}
    if exp:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(seconds=parse_duration(exp))
    if claims:
        payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")
