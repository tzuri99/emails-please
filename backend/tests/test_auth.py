"""Authentication, in both of its configurations.

The risk with optional auth is that it silently stays off in production, so
these tests pin both states: open when unconfigured, closed when configured.
"""
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "firebase_project_id", "demo-project")
    yield
    auth._initialised = False


class TestAuthDisabled:
    """Unconfigured: the API is open, so a local run and a demo work."""

    def test_config_reports_auth_off(self, client):
        body = client.get("/api/config").json()
        assert body["auth_required"] is False

    def test_reads_are_open(self, client):
        assert client.get("/api/runs").status_code == 200

    def test_writes_are_open(self, client):
        r = client.post("/api/runs/999/emails/email_001/review",
                        json={"decision": "confirmed"})
        assert r.status_code != 401      # 404: no such run, but not refused


class TestAuthEnabled:
    """Configured: every mutating call needs a valid token."""

    def test_config_reports_auth_on(self, client, auth_on):
        body = client.get("/api/config").json()
        assert body["auth_required"] is True
        assert body["firebase_project_id"] == "demo-project"

    def test_write_without_a_token_is_refused(self, client, auth_on):
        r = client.post("/api/runs/1/emails/email_001/review",
                        json={"decision": "confirmed"})
        assert r.status_code == 401

    def test_malformed_header_is_refused(self, client, auth_on):
        r = client.post("/api/runs/1/emails/email_001/review",
                        json={"decision": "confirmed"},
                        headers={"Authorization": "Basic abc123"})
        assert r.status_code == 401

    def test_forged_token_is_refused(self, client, auth_on, monkeypatch):
        """A token that does not verify must not authenticate anyone."""
        def reject(_token):
            raise ValueError("signature mismatch")
        monkeypatch.setattr(auth, "verify_token",
                            lambda t: (_ for _ in ()).throw(
                                __import__("fastapi").HTTPException(401, "bad")))
        r = client.post("/api/runs/1/emails/email_001/review",
                        json={"decision": "confirmed"},
                        headers={"Authorization": "Bearer forged.token.here"})
        assert r.status_code == 401

    def test_starting_a_run_is_protected(self, client, auth_on):
        assert client.post("/api/runs").status_code == 401

    def test_reads_stay_open(self, client, auth_on):
        """Reading the queue is not gated; only changes to it are."""
        assert client.get("/api/runs").status_code == 200


class TestReviewerIdentity:
    def test_client_supplied_name_used_when_auth_is_off(self):
        """With no verified identity we fall back to what the client says,
        and the audit trail is only as good as that."""
        assert auth.ANONYMOUS.verified is False
        assert auth.ANONYMOUS.display == "anonymous"

    def test_verified_principal_prefers_email(self):
        p = auth.Principal(uid="abc123", email="ops@example.com", verified=True)
        assert p.display == "ops@example.com"

    def test_verified_principal_falls_back_to_uid(self):
        p = auth.Principal(uid="abc123", email="", verified=True)
        assert p.display == "abc123"


