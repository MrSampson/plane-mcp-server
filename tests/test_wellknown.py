"""The ChatGPT app-directory domain-verification route."""

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from plane_mcp.wellknown import (
    OPENAI_APPS_CHALLENGE_ENV,
    OPENAI_APPS_CHALLENGE_PATH,
    openai_apps_challenge_routes,
)

TOKEN = "41YeOptxH4mEJr3dGUJC-LuzUKvtqyDlSuMI172A3wQ"


def _client(**kw) -> TestClient:
    return TestClient(Starlette(routes=openai_apps_challenge_routes(**kw)))


def test_serves_token_verbatim_as_plain_text():
    response = _client(token=TOKEN).get(OPENAI_APPS_CHALLENGE_PATH)
    assert response.status_code == 200
    assert response.text == TOKEN
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"


def test_absent_when_no_token_is_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(OPENAI_APPS_CHALLENGE_ENV, raising=False)
    assert openai_apps_challenge_routes() == []
    assert _client(token="").get(OPENAI_APPS_CHALLENGE_PATH).status_code == 404


def test_reads_token_from_environment_and_strips_whitespace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(OPENAI_APPS_CHALLENGE_ENV, f"  {TOKEN}\n")
    assert _client().get(OPENAI_APPS_CHALLENGE_PATH).text == TOKEN


def test_only_get_is_served():
    assert _client(token=TOKEN).post(OPENAI_APPS_CHALLENGE_PATH).status_code == 405
