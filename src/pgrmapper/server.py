"""Run the pgrmapper FastAPI app with uvicorn.

Optional mTLS: when PGMAPPER_TLS_CERT and PGMAPPER_TLS_KEY are set, the
server speaks TLS; PGMAPPER_TLS_CA additionally makes it require a
CA-signed client certificate (mutual TLS).
"""

import os
import ssl

import uvicorn


def main() -> None:
    port = int(os.environ.get("PGREMAPPER_PORT", "8000"))
    host = os.environ.get("PGREMAPPER_HOST", "0.0.0.0")
    kwargs: dict = {}
    cert = os.environ.get("PGMAPPER_TLS_CERT")
    key = os.environ.get("PGMAPPER_TLS_KEY")
    if cert and key:
        kwargs["ssl_certfile"] = cert
        kwargs["ssl_keyfile"] = key
        ca = os.environ.get("PGMAPPER_TLS_CA")
        if ca:
            kwargs["ssl_ca_certs"] = ca
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
    uvicorn.run("pgrmapper.app:app", host=host, port=port, **kwargs)
