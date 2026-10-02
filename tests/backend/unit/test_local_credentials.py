"""Credential persistence, recovery and identity boundaries through the real API."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
import respx
from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic import ValidationError
from sqlalchemy import select

from paperwrench.config import Settings
from paperwrench.db.engine import get_session_factory
from paperwrench.db.models import LocalCredential
from paperwrench.db.models import PaperlessIdentity
from paperwrench.db.models import UserPreference
from paperwrench.main import create_app

TOKEN = "alice-paperless-private-token"
PASSWORD = "alice-local-long-password"
PROFILE = {"id": 42, "username": "alice", "first_name": "Alice"}
PROFILE_URL = "http://paperless.test/api/profile/"


@pytest.fixture
def credential_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"credential_key": SecretStr(Fernet.generate_key().decode())})


@pytest.fixture
def vault_client(credential_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(credential_settings)) as client:
        yield client


def enroll(client: TestClient, token: str = TOKEN, password: str = PASSWORD) -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/v1/auth/login",
        json={
            "token": token,
            "remember": True,
            "password": password,
            "locale": "fr",
        },
    )
    return response


def login(client: TestClient, username: str = "alice", password: str = PASSWORD) -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/v1/auth/password-login",
        json={
            "username": username,
            "password": password,
        },
    )
    return response


@respx.mock
def test_enrollment_encrypts_token_and_hashes_password(vault_client: TestClient) -> None:
    respx.get(PROFILE_URL).respond(200, json=PROFILE)
    assert vault_client.get("/api/v1/auth/options").json() == {"remember_available": True}
    response = enroll(vault_client)
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    assert response.json()["remembered"] is True
    assert TOKEN not in response.text and PASSWORD not in response.text
    with get_session_factory()() as db:
        credential = db.get(LocalCredential, 42)
        assert credential is not None
        assert TOKEN not in credential.encrypted_token
        assert PASSWORD not in credential.password_hash
        assert credential.password_hash.startswith("$argon2id$")
        assert PasswordHasher().verify(credential.password_hash, PASSWORD)
        assert credential.username == "alice"
    assert response.headers["cache-control"] == "no-store"


@respx.mock
def test_restart_requires_local_login_and_revalidates_upstream(
    credential_settings: Settings,
    db_path: Path,
) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    with TestClient(create_app(credential_settings)) as first:
        response = enroll(first)
        assert response.status_code == 200
        cookie = first.cookies["paperwrench_session"]
    for file in db_path.parent.glob("test.db*"):
        content = file.read_bytes()
        assert TOKEN.encode() not in content and PASSWORD.encode() not in content
        assert credential_settings.credential_key.get_secret_value().encode() not in content
    with TestClient(create_app(credential_settings)) as second:
        second.cookies.set("paperwrench_session", cookie)
        assert second.get("/api/v1/auth/me").status_code == 401
        second.cookies.clear()
        response = login(second)
        assert response.status_code == 200
        assert response.json()["user_id"] == 42
        assert response.json()["locale"] == "fr"
        assert route.call_count == 2
        assert second.cookies["paperwrench_session"] != cookie
        assert "httponly" in response.headers["set-cookie"].lower()


@respx.mock
def test_unknown_user_and_wrong_password_have_same_error(vault_client: TestClient) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    enroll(vault_client)
    vault_client.cookies.clear()
    unknown = login(vault_client, "unknown")
    wrong = login(vault_client, password="wrong-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert route.call_count == 1
    assert "paperwrench_session" not in unknown.cookies


@respx.mock
def test_revoked_token_can_be_replaced_without_old_local_password(vault_client: TestClient) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    assert enroll(vault_client).status_code == 200
    vault_client.cookies.clear()
    route.respond(401)
    rejected = login(vault_client)
    assert rejected.status_code == 401 and TOKEN not in rejected.text
    route.respond(200, json=PROFILE)
    assert enroll(vault_client, "replacement-token", "new-local-password").status_code == 200
    assert login(vault_client).status_code == 401
    accepted = login(vault_client, password="new-local-password")
    assert accepted.status_code == 200
    assert route.calls.last.request.headers["authorization"] == "Token replacement-token"


@respx.mock
def test_ephemeral_and_globally_disabled_storage(vault_client: TestClient) -> None:
    respx.get(PROFILE_URL).respond(200, json=PROFILE)
    response = vault_client.post("/api/v1/auth/login", json={"token": TOKEN, "remember": False})
    assert response.status_code == 200 and response.json()["remembered"] is False
    with get_session_factory()() as db:
        assert db.scalar(select(LocalCredential)) is None
    assert enroll(vault_client).status_code == 200
    app = cast(FastAPI, vault_client.app)
    app.state.credential_vault.settings.remember_tokens = False
    assert vault_client.get("/api/v1/auth/options").json() == {"remember_available": False}
    assert enroll(vault_client).status_code == 503
    assert login(vault_client).status_code == 503
    assert vault_client.post("/api/v1/auth/login", json={"token": TOKEN}).status_code == 200
    # Disabling does not trap existing encrypted credentials: deletion still works.
    csrf = vault_client.get("/api/v1/auth/me").json()["csrf_token"]
    assert (
        vault_client.delete("/api/v1/auth/credentials", headers={"X-CSRF-Token": csrf}).status_code
        == 204
    )


@respx.mock
def test_missing_key_preserves_token_login(unauthenticated_client: TestClient) -> None:
    respx.get(PROFILE_URL).respond(200, json=PROFILE)
    assert unauthenticated_client.get("/api/v1/auth/options").json()["remember_available"] is False
    assert enroll(unauthenticated_client).status_code == 503
    assert (
        unauthenticated_client.post("/api/v1/auth/login", json={"token": TOKEN}).status_code == 200
    )


@respx.mock
@pytest.mark.parametrize(
    "profile",
    [
        {"email": "alice@example.test", "auth_token": TOKEN},
        {"id": 42, "email": "alice@example.test"},
        {"username": "alice"},
        {"id": -42, "username": "alice"},
    ],
)
def test_fabricated_identity_cannot_enroll(
    vault_client: TestClient, profile: dict[str, object]
) -> None:
    respx.get(PROFILE_URL).respond(200, json=profile)
    respx.get("http://paperless.test/api/ui_settings/").respond(403)
    rejected = enroll(vault_client)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AUTH_IDENTITY_UNSUPPORTED"
    assert not cast(FastAPI, vault_client.app).state.sessions._sessions
    assert vault_client.post("/api/v1/auth/login", json={"token": TOKEN}).status_code == 200


@respx.mock
def test_username_is_server_assigned_and_cannot_transfer_identity(vault_client: TestClient) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    assert (
        vault_client.post(
            "/api/v1/auth/login",
            json={
                "token": TOKEN,
                "remember": True,
                "password": PASSWORD,
                "username": "chosen",
            },
        ).status_code
        == 422
    )
    assert enroll(vault_client).status_code == 200
    route.respond(200, json={**PROFILE, "id": 99})
    response = enroll(vault_client, "other-user-token")
    assert response.status_code == 409
    assert login(vault_client).status_code == 401
    with get_session_factory()() as db:
        assert db.get(LocalCredential, 42) is not None
        assert db.get(LocalCredential, 99) is None


@respx.mock
def test_rotation_requires_csrf_and_same_identity_and_revokes_old_sessions(
    vault_client: TestClient,
) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    original = enroll(vault_client)
    old_cookie = vault_client.cookies["paperwrench_session"]
    csrf = original.json()["csrf_token"]
    body = {"token": "rotated-token", "password": "rotated-password"}
    assert vault_client.put("/api/v1/auth/credentials", json=body).status_code == 403
    route.respond(200, json={"id": 99, "username": "bob"})
    headers = {"X-CSRF-Token": csrf}
    assert (
        vault_client.put("/api/v1/auth/credentials", json=body, headers=headers).status_code == 409
    )
    route.respond(200, json=PROFILE)
    replaced = vault_client.put("/api/v1/auth/credentials", json=body, headers=headers)
    assert replaced.status_code == 200
    assert vault_client.cookies["paperwrench_session"] != old_cookie
    app = cast(FastAPI, vault_client.app)
    assert old_cookie not in app.state.sessions._sessions
    assert login(vault_client).status_code == 401
    assert login(vault_client, password="rotated-password").status_code == 200


@respx.mock
def test_deletion_is_owner_scoped_and_revokes_all_owner_sessions(vault_client: TestClient) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    alice = enroll(vault_client)
    alice_cookie = vault_client.cookies["paperwrench_session"]
    vault_client.cookies.clear()
    route.respond(200, json={"id": 99, "username": "bob"})
    assert enroll(vault_client, "bob-token").status_code == 200
    bob_cookie = vault_client.cookies["paperwrench_session"]
    vault_client.cookies.clear()
    vault_client.cookies.set("paperwrench_session", alice_cookie)
    assert vault_client.delete("/api/v1/auth/credentials").status_code == 403
    deleted = vault_client.delete(
        "/api/v1/auth/credentials", headers={"X-CSRF-Token": alice.json()["csrf_token"]}
    )
    assert deleted.status_code == 204
    app = cast(FastAPI, vault_client.app)
    assert alice_cookie not in app.state.sessions._sessions
    assert bob_cookie in app.state.sessions._sessions
    with get_session_factory()() as db:
        assert db.get(LocalCredential, 42) is None
        assert db.get(LocalCredential, 99) is not None
    assert login(vault_client).status_code == 401


@respx.mock
@pytest.mark.parametrize("change", ["ciphertext", "key", "instance"])
def test_tampering_or_key_loss_fails_closed(vault_client: TestClient, change: str) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    enroll(vault_client)
    vault_client.cookies.clear()
    if change == "ciphertext":
        with get_session_factory()() as db:
            row = db.get(LocalCredential, 42)
            assert row is not None
            row.encrypted_token = "corrupted"
            db.commit()
    else:
        vault = cast(FastAPI, vault_client.app).state.credential_vault
        if change == "key":
            vault._cipher = Fernet(Fernet.generate_key())
        else:
            vault.settings.paperless_url = "http://another-instance.test"
    response = login(vault_client)
    assert response.status_code == 401
    assert route.call_count == 1
    assert TOKEN not in response.text and PASSWORD not in response.text


@respx.mock
def test_deletion_during_upstream_validation_cannot_restore_a_session(
    vault_client: TestClient,
) -> None:
    route = respx.get(PROFILE_URL).respond(200, json=PROFILE)
    enroll(vault_client)
    vault_client.cookies.clear()

    def delete_while_validating(request: httpx.Request) -> httpx.Response:
        with get_session_factory()() as db:
            row = db.get(LocalCredential, 42)
            assert row is not None
            db.delete(row)
            db.commit()
        return httpx.Response(200, json=PROFILE)

    route.mock(side_effect=delete_while_validating)
    response = login(vault_client)
    assert response.status_code == 401
    assert "paperwrench_session" not in response.cookies


def test_password_attempts_are_bounded(vault_client: TestClient) -> None:
    for _ in range(10):
        assert login(vault_client, "unknown").status_code == 401
    assert login(vault_client, "unknown").status_code == 429


@respx.mock
def test_cross_origin_and_invalid_passwords_never_enroll(vault_client: TestClient) -> None:
    assert (
        vault_client.post(
            "/api/v1/auth/password-login",
            json={
                "username": "alice",
                "password": PASSWORD,
            },
            headers={"Origin": "https://hostile.test"},
        ).status_code
        == 403
    )
    response = enroll(vault_client, password="tiny-pw")
    assert response.status_code == 422
    assert "tiny-pw" not in response.text and TOKEN not in response.text
    response = vault_client.post("/api/v1/auth/login", json={"token": TOKEN, "remember": True})
    assert response.status_code == 422


def test_key_file_precedence_and_safe_configuration_errors(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    file = tmp_path / "credential-key"
    file.write_text(key + "\n", encoding="utf-8")
    settings = Settings(
        PAPERWRENCH_CREDENTIAL_KEY="bad-inline", PAPERWRENCH_CREDENTIAL_KEY_FILE=file
    )
    assert settings.credential_storage_available
    assert settings.credential_key.get_secret_value() == key
    assert key not in str(settings)
    with pytest.raises(ValidationError) as error:
        Settings(PAPERWRENCH_CREDENTIAL_KEY="bad-inline")
    assert "bad-inline" not in str(error.value)
    with pytest.raises(ValidationError):
        Settings(
            PAPERWRENCH_CREDENTIAL_KEY=key, PAPERWRENCH_CREDENTIAL_KEY_FILE=tmp_path / "missing"
        )


@respx.mock
def test_321_identity_preserves_existing_owner_across_rotation_and_forgetting(
    vault_client: TestClient,
) -> None:
    respx.get(PROFILE_URL).respond(200, json={"email": "alice@example.test", "auth_token": TOKEN})
    identity = respx.get("http://paperless.test/api/ui_settings/").respond(
        200,
        json={"user": PROFILE, "settings": {}, "permissions": []},
    )
    legacy_owner = int.from_bytes(hashlib.sha256(TOKEN.encode()).digest()[:8], "big") & (
        (1 << 63) - 1
    )
    with get_session_factory()() as db:
        db.add(UserPreference(owner_id=legacy_owner, locale="en"))
        db.commit()
    response = enroll(vault_client)
    assert response.status_code == 200
    assert response.json()["username"] == "alice"  # Never the email or display name.
    assert response.json()["user_id"] == legacy_owner
    assert response.json()["locale"] == "en"
    with get_session_factory()() as db:
        binding = db.get(PaperlessIdentity, legacy_owner)
        assert binding is not None and binding.upstream_user_id == 42
    rotation = vault_client.put(
        "/api/v1/auth/credentials",
        json={
            "token": "different-paperless-token",
            "password": "different-local-password",
        },
        headers={"X-CSRF-Token": response.json()["csrf_token"]},
    )
    assert rotation.status_code == 200
    assert rotation.json()["user_id"] == legacy_owner
    vault_client.cookies.clear()
    password_login = login(vault_client, password="different-local-password")
    assert password_login.status_code == 200
    assert password_login.json()["user_id"] == legacy_owner
    # Revalidation compares the token's identity, not its historical owner key.
    app = cast(FastAPI, vault_client.app)
    app.state.sessions.settings.session_revalidate_seconds = 0
    assert vault_client.get("/api/v1/auth/me").status_code == 200
    deleted = vault_client.delete(
        "/api/v1/auth/credentials",
        headers={
            "X-CSRF-Token": password_login.json()["csrf_token"],
        },
    )
    assert deleted.status_code == 204
    vault_client.cookies.clear()
    ephemeral = vault_client.post("/api/v1/auth/login", json={"token": "different-paperless-token"})
    assert ephemeral.status_code == 200
    assert ephemeral.json()["user_id"] == legacy_owner
    assert ephemeral.json()["remembered"] is False
    assert identity.call_count >= 5


@respx.mock
def test_321_recovery_token_cannot_rebind_another_users_account(vault_client: TestClient) -> None:
    respx.get(PROFILE_URL).respond(200, json={"email": "shared@example.test"})
    identity = respx.get("http://paperless.test/api/ui_settings/").respond(
        200, json={"user": PROFILE}
    )
    alice = enroll(vault_client)
    assert alice.status_code == 200
    owner = alice.json()["user_id"]
    identity.respond(200, json={"user": {"id": 99, "username": "alice"}})
    assert enroll(vault_client, token="bob-private-token").status_code == 409
    assert login(vault_client).status_code == 401
    with get_session_factory()() as db:
        binding = db.get(PaperlessIdentity, owner)
        assert binding is not None and binding.upstream_user_id == 42


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 500])
def test_321_unavailable_identity_keeps_ephemeral_login(
    vault_client: TestClient, status: int
) -> None:
    respx.get(PROFILE_URL).respond(200, json={"email": "alice@example.test"})
    respx.get("http://paperless.test/api/ui_settings/").respond(status)
    assert enroll(vault_client).status_code == 409
    assert vault_client.post("/api/v1/auth/login", json={"token": TOKEN}).status_code == 200
