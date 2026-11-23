"""System settings, platform credentials and user management."""
from __future__ import annotations

from app.services import settings_service


def test_settings_are_admin_only(client, admin, user):
    _, headers = user
    assert client.get("/api/settings", headers=headers).status_code == 403
    assert client.patch("/api/settings", json={}, headers=headers).status_code == 403
    assert client.get("/api/users", headers=headers).status_code == 403
    body = client.get("/api/settings", headers=admin).json()
    assert body["allow_registration"] is True
    assert set(body["enabled_platforms"]) == {"x", "bluesky", "reddit"}


def test_credentials_are_saved_encrypted_and_never_returned(client, admin):
    resp = client.patch(
        "/api/settings",
        json={"credentials": {"x_bearer_token": "secret-token", "reddit_user_agent": "ua/2"}},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["credentials"]["x_bearer_token"] == "saved"
    assert body["credentials"]["reddit_client_secret"] == "unset"
    assert body["credential_values"]["reddit_user_agent"] == "ua/2"
    assert "secret-token" not in resp.text
    assert "x_bearer_token" not in body["credential_values"]

    options = client.get("/api/options", headers=admin).json()
    by_platform = {p["value"]: p for p in options["platforms"]}
    assert by_platform["x"]["configured"] is True
    assert by_platform["reddit"]["configured"] is False
    assert by_platform["bluesky"]["configured"] is True

    # Clearing reverts to the env var (empty in tests).
    body = client.patch(
        "/api/settings", json={"credentials": {"x_bearer_token": ""}}, headers=admin
    ).json()
    assert body["credentials"]["x_bearer_token"] == "unset"
    assert client.patch(
        "/api/settings", json={"credentials": {"nope": "x"}}, headers=admin
    ).status_code == 422


def test_platform_credentials_prefer_saved_values():
    from app.core.security import encrypt_secret
    from app.models.system_settings import SystemSettings

    row = SystemSettings(credentials={"x_bearer_token": encrypt_secret("abc")})
    assert settings_service.platform_credentials(row)["x_bearer_token"] == "abc"
    row = SystemSettings(credentials={"x_bearer_token": "not-encrypted"})
    assert settings_service.platform_credentials(row)["x_bearer_token"] == ""


def test_interval_defaults_must_be_consistent(client, admin):
    resp = client.patch(
        "/api/settings", json={"min_scan_interval_minutes": 120}, headers=admin
    )
    assert resp.status_code == 422


def test_registration_can_be_turned_off(client, admin):
    client.patch("/api/settings", json={"allow_registration": False}, headers=admin)
    assert client.get("/api/auth/config").json()["registration_enabled"] is False
    resp = client.post(
        "/api/auth/register", json={"email": "x@example.com", "password": "password123"}
    )
    assert resp.status_code == 403


def test_admin_creates_and_manages_users(client, admin):
    resp = client.post(
        "/api/users",
        json={"email": "made@example.com", "password": "password123", "display_name": "Made"},
        headers=admin,
    )
    assert resp.status_code == 201
    made = resp.json()
    assert made["is_enabled"] is True and made["role"] == "user"

    resp = client.patch(f"/api/users/{made['id']}", json={"role": "admin"}, headers=admin)
    assert resp.json()["role"] == "admin"
    resp = client.patch(f"/api/users/{made['id']}", json={"is_active": False}, headers=admin)
    assert resp.json()["is_active"] is False
    resp = client.post("/api/auth/login", json={"email": "made@example.com",
                                                 "password": "password123"})
    assert resp.status_code == 401

    me = client.get("/api/auth/me", headers=admin).json()
    # The last active admin can't demote or remove themselves.
    assert client.patch(f"/api/users/{me['id']}", json={"role": "user"},
                        headers=admin).status_code == 422
    assert client.delete(f"/api/users/{me['id']}", headers=admin).status_code == 422
    assert client.delete(f"/api/users/{made['id']}", headers=admin).status_code == 204


def test_update_me(client, user):
    _, headers = user
    resp = client.patch("/api/auth/me", json={"display_name": "New Name"}, headers=headers)
    assert resp.json()["display_name"] == "New Name"
    resp = client.patch(
        "/api/auth/me",
        json={"current_password": "wrong", "new_password": "another-pass"},
        headers=headers,
    )
    assert resp.status_code == 422
    resp = client.patch(
        "/api/auth/me",
        json={"current_password": "user-password", "new_password": "another-pass"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert client.post("/api/auth/login", json={"email": "user@example.com",
                                                 "password": "another-pass"}).status_code == 200
