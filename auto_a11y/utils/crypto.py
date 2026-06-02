"""Symmetric encryption for credentials stored at rest (test-site logins).

``ProjectUser`` / ``WebsiteUser`` hold the username + password of accounts
used to log into the sites under test. These must not sit in MongoDB in
plaintext. This module provides opt-in, backward-compatible Fernet
encryption for those secrets.

**Key management decision.** Encryption is driven by a single Fernet key
read from ``Config.CREDENTIAL_ENCRYPTION_KEY`` (env ``CREDENTIAL_ENCRYPTION_KEY``),
a urlsafe-base64 32-byte key generated with
``cryptography.fernet.Fernet.generate_key()``. The key is intentionally
*separate* from ``SECRET_KEY`` so it can be rotated and access-controlled
independently of Flask session signing.

**Rollout is non-breaking and gradual:**

* No key configured → :func:`encrypt_credential` stores plaintext (a one-time
  warning is logged). Existing deployments keep working unchanged.
* Encrypted values are tagged with the :data:`_ENC_PREFIX` marker, so
  :func:`decrypt_credential` can tell an encrypted value from a legacy
  plaintext one. Legacy plaintext (no marker) is returned as-is, so old
  documents read correctly; the next write re-stores them encrypted once a
  key is configured.
* A value that *is* encrypted but for which no key is available raises
  :class:`CredentialDecryptionError` rather than silently returning a wrong
  value — losing the key must fail loudly, not corrupt logins.

Key rotation and bulk re-encryption of existing rows are out of scope here;
the marker scheme supports a future migration command.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Marker prefixing every value this module encrypts. Lets us distinguish an
# encrypted value from a legacy plaintext one without a schema change.
_ENC_PREFIX = "enc:fernet:v1:"

_warned_no_key = False


class CredentialDecryptionError(Exception):
    """Raised when an encrypted credential cannot be decrypted.

    Typically means ``CREDENTIAL_ENCRYPTION_KEY`` is missing or does not
    match the key the value was encrypted with.
    """


def _get_key() -> bytes | None:
    """Return the configured Fernet key as bytes, or ``None`` if unset."""
    from config import Config

    raw = getattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "") or ""
    return raw.encode("utf-8") if raw else None


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a secret for storage at rest.

    Returns a marker-prefixed Fernet token when a key is configured, or the
    original plaintext (logging a one-time warning) when it is not — so the
    feature is opt-in and never blocks writes on an unconfigured deployment.
    """
    global _warned_no_key
    key = _get_key()
    if key is None:
        if not _warned_no_key:
            logger.warning(
                "CREDENTIAL_ENCRYPTION_KEY is not set; storing test-user "
                + "credentials in plaintext. Set a Fernet key to encrypt "
                + "them at rest."
            )
            _warned_no_key = True
        return plaintext
    # Already encrypted (e.g. a value round-tripped without decryption) —
    # don't double-encrypt.
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext
    from cryptography.fernet import Fernet

    token = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def decrypt_credential(stored: str) -> str:
    """Decrypt a value produced by :func:`encrypt_credential`.

    Legacy plaintext (without the marker) is returned unchanged, so existing
    documents keep working. A marker-tagged value with no/!wrong key raises
    :class:`CredentialDecryptionError`.
    """
    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext, stored before encryption was enabled
    key = _get_key()
    if key is None:
        raise CredentialDecryptionError(
            "Stored credential is encrypted but CREDENTIAL_ENCRYPTION_KEY is "
            + "not set; cannot decrypt."
        )
    from cryptography.fernet import Fernet, InvalidToken

    token = stored[len(_ENC_PREFIX):].encode("ascii")
    try:
        return Fernet(key).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Failed to decrypt stored credential; the configured "
            + "CREDENTIAL_ENCRYPTION_KEY may be wrong."
        ) from exc
