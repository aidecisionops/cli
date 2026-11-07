from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def create_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def describe_tls_setup() -> dict[str, str]:
    try:
        import certifi
    except ImportError:
        return {"ssl_backend": ssl.OPENSSL_VERSION, "ca_source": "system trust store"}
    return {"ssl_backend": ssl.OPENSSL_VERSION, "ca_source": certifi.where()}
