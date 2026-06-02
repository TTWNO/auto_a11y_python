"""Tests for at-rest encryption of test-user credentials."""
from __future__ import annotations

import os

os.environ.setdefault("RUN_AI_ANALYSIS", "false")

import pytest
from cryptography.fernet import Fernet

from config import Config
from auto_a11y.utils.crypto import (
    CredentialDecryptionError,
    decrypt_credential,
    encrypt_credential,
)
from auto_a11y.models.project_user import ProjectUser
from auto_a11y.models.website_user import WebsiteUser


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a real Fernet key for the duration of a test."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", key, raising=False)
    return key


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "", raising=False)


def test_encrypt_then_decrypt_round_trips(with_key: str) -> None:
    token = encrypt_credential("hunter2")
    assert token != "hunter2"
    assert token.startswith("enc:fernet:v1:")
    assert decrypt_credential(token) == "hunter2"


def test_no_key_stores_plaintext(no_key: None) -> None:
    assert encrypt_credential("hunter2") == "hunter2"
    # legacy/plaintext reads back unchanged
    assert decrypt_credential("hunter2") == "hunter2"


def test_legacy_plaintext_read_with_key_present(with_key: str) -> None:
    # A value stored before encryption was enabled has no marker; it must
    # still read back as-is even though a key is now configured.
    assert decrypt_credential("old-plaintext-pw") == "old-plaintext-pw"


def test_encrypted_value_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", key, raising=False)
    token = encrypt_credential("secret")
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "", raising=False)
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(token)


def test_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Config, "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"), raising=False,
    )
    token = encrypt_credential("secret")
    monkeypatch.setattr(
        Config, "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"), raising=False,
    )
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(token)


def test_double_encrypt_is_idempotent(with_key: str) -> None:
    once = encrypt_credential("secret")
    twice = encrypt_credential(once)
    assert twice == once
    assert decrypt_credential(twice) == "secret"


def test_project_user_password_encrypted_in_to_dict(with_key: str) -> None:
    user = ProjectUser(project_id="p1", username="tester", password="s3cret")
    data = user.to_dict()
    assert data["password"] != "s3cret"
    assert data["password"].startswith("enc:fernet:v1:")
    restored = ProjectUser.from_dict(data)
    assert restored.password == "s3cret"


def test_website_user_password_encrypted_in_to_dict(with_key: str) -> None:
    user = WebsiteUser(website_id="w1", username="tester", password="s3cret")
    data = user.to_dict()
    assert data["password"] != "s3cret"
    assert data["password"].startswith("enc:fernet:v1:")
    restored = WebsiteUser.from_dict(data)
    assert restored.password == "s3cret"


def test_project_user_legacy_plaintext_password_reads(with_key: str) -> None:
    # A document written before encryption: password is bare plaintext.
    restored = ProjectUser.from_dict(
        {"project_id": "p1", "username": "t", "password": "legacy-pw"}
    )
    assert restored.password == "legacy-pw"
