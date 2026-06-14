"""Fernet-based at-rest encryption for secret DB columns.

Set DB_SECRETS_KEY to a base64-url-encoded 32-byte Fernet key.
Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

When the env var is absent, the TypeDecorator passes values through unmodified so
existing deployments remain functional.  Set the var in production to enable encryption.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import String, types

log = logging.getLogger(__name__)

_fernet = None
_key_loaded = False


def _get_fernet():
    global _fernet, _key_loaded
    if _key_loaded:
        return _fernet
    _key_loaded = True
    raw = os.environ.get("DB_SECRETS_KEY", "")
    if not raw:
        log.warning(
            "DB_SECRETS_KEY not set — secret DB columns stored as plaintext. "
            "Set this env var to enable at-rest encryption."
        )
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(raw.encode())
        log.info("DB at-rest encryption enabled for secret columns.")
        return _fernet
    except Exception as exc:
        log.error("Invalid DB_SECRETS_KEY — secret columns will be plaintext: %s", exc)
        return None


class EncryptedString(types.TypeDecorator):
    """Stores a string as Fernet-encrypted ciphertext when DB_SECRETS_KEY is set."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not value:
            return value
        f = _get_fernet()
        if f is None:
            return value
        return f.encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if not value:
            return value
        f = _get_fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except Exception:
            # Value stored before encryption was enabled — return as-is
            return value
