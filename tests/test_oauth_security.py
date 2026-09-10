"""End-to-end test for the OAuth redirect attack reported by security researcher.

Attack scenario:
  1. Attacker registers a malicious OAuth client with redirect_uri=https://attacker.com/steal
  2. Attacker crafts an authorization URL and tricks victim into visiting it
  3. Victim approves on the legitimate-looking consent page
  4. Server redirects auth code to attacker's domain
  5. Attacker exchanges code for access token cross-origin (enabled by CORS credentials:true)
  6. Attacker has full read/write access to victim's Plane workspace

This test replays the attack against the real server app and verifies each
security fix blocks the corresponding step.
"""

import pytest
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from plane_mcp.auth import PlaneOAuthProvider
from plane_mcp.server import DEFAULT_ALLOWED_REDIRECT_URIS, get_allowed_client_redirect_uris


def _build_app(allowed_client_redirect_uris: list[str]) -> Starlette:
    """Build the OAuth app with the given redirect allowlist.

    Mirrors the security-relevant wiring from plane_mcp/server.py and
    plane_mcp/__main__.py (auth provider config + CORS). It mounts only the
    OAuth app, not __main__'s full three-mount topology (header + oauth +
    sse) — the attack surface under test lives entirely in the OAuth
    provider and CORS layers, not in mount routing.
    """
    oauth_mcp = FastMCP(
        "Plane MCP Server",
        auth=PlaneOAuthProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            base_url="http://localhost:8211",
            plane_base_url="http://localhost:9999",
            plane_internal_base_url="http://localhost:9999",
            client_storage=MemoryStore(),
            required_scopes=["read", "write"],
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            require_authorization_consent=False,
        ),
    )

    oauth_app = oauth_mcp.http_app()

    starlette_app = Starlette(
        routes=[Mount("/", app=oauth_app)],
    )

    # Same CORS config as __main__.py
    starlette_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return starlette_app


def _register_client(client: TestClient, redirect_uri: str) -> dict:
    """Register an OAuth client with the given redirect_uri."""
    response = client.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert response.status_code == 201, f"Registration failed: {response.text}"
    data = response.json()
    assert "client_id" in data
    return data


@pytest.fixture(scope="module")
def app():
    """Build the OAuth app using the real, production allowlist — the exact
    output of get_allowed_client_redirect_uris(), not a hand-copied list."""
    return _build_app(get_allowed_client_redirect_uris())


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, follow_redirects=False)


class TestOAuthRedirectAttack:
    """Replay the full attack chain and verify each fix blocks it.

    The server uses an OAuth proxy architecture: /authorize redirects to
    upstream Plane OAuth (not directly to the client's redirect_uri). After
    Plane approves, the server's /auth/callback handles the response and
    only then redirects to the MCP client's redirect_uri.

    The allowed_client_redirect_uris patterns restrict which client URIs
    the proxy will redirect to after the upstream callback completes.
    """

    def test_full_attack_is_blocked(self, client: TestClient) -> None:
        """Replay the exact attack: register with attacker URI, hit /authorize.

        Even though the proxy architecture means /authorize redirects to
        upstream Plane OAuth (not directly to the attacker), the server
        must never include the attacker's URI anywhere in the redirect chain.
        """
        attacker_uri = "https://attacker.com/steal"

        # Step 1: Attacker registers malicious client
        reg = _register_client(client, attacker_uri)

        # Step 2: Attacker crafts authorization URL for victim
        response = client.get(
            "/authorize",
            params={
                "client_id": reg["client_id"],
                "redirect_uri": attacker_uri,
                "response_type": "code",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )

        # The attacker's domain must never appear in any redirect
        location = response.headers.get("location", "")
        assert "attacker.com" not in location, f"VULNERABILITY: Attacker domain found in redirect! Location: {location}"

        # If the server responds with a redirect, it should be to the upstream
        # Plane OAuth provider — with the *server's own* callback URI, not the attacker's
        if response.is_redirect:
            assert "localhost" in location, f"Redirect should go to upstream Plane OAuth (localhost), got: {location}"
            # The redirect_uri param in the upstream redirect must point to the
            # server's /auth/callback, NOT to the attacker
            assert "attacker" not in location

    @pytest.mark.parametrize(
        "malicious_uri",
        [
            "https://attacker.com/steal",
            "http://evil.com/callback",
            "javascript:alert(document.cookie)",
            "data:text/html,<script>fetch('https://evil.com')</script>",
            "myapp://callback",
        ],
        ids=[
            "attacker-domain",
            "evil-domain",
            "javascript-injection",
            "data-uri-injection",
            "unknown-protocol",
        ],
    )
    def test_malicious_uris_never_appear_in_redirects(self, client: TestClient, malicious_uri: str) -> None:
        """Verify various attack vectors never leak into redirect locations."""
        reg = _register_client(client, malicious_uri)

        response = client.get(
            "/authorize",
            params={
                "client_id": reg["client_id"],
                "redirect_uri": malicious_uri,
                "response_type": "code",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )

        # The malicious URI must not appear in any redirect location
        location = response.headers.get("location", "")
        if response.is_redirect:
            # Extract the redirect_uri parameter from the upstream redirect
            # It must be the server's /auth/callback, not the malicious URI
            assert malicious_uri not in location, f"VULNERABILITY: Malicious URI leaked into redirect: {location}"

    def test_legitimate_redirect_uri_passes(self, client: TestClient) -> None:
        """Sanity check: legitimate localhost URI is accepted and the flow proceeds."""
        legitimate_uri = "http://localhost:3000/callback"

        reg = _register_client(client, legitimate_uri)

        response = client.get(
            "/authorize",
            params={
                "client_id": reg["client_id"],
                "redirect_uri": legitimate_uri,
                "response_type": "code",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )

        # Should proceed with the OAuth flow (302 to upstream), not error
        assert response.status_code != 400, f"Legitimate redirect URI was rejected: {response.text}"

    def test_cors_blocks_cross_origin_token_theft(self, client: TestClient) -> None:
        """Step 5: Even if attacker got a code, CORS blocks cross-origin token exchange.

        With allow_credentials=False, browsers will not attach cookies or allow
        attacker JS to read the response from a cross-origin POST to /token.
        The combination of Access-Control-Allow-Origin: * with credentials:true
        was the original vulnerability — now credentials is false.
        """
        response = client.options(
            "/token",
            headers={
                "Origin": "https://attacker.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        # Credentials must NOT be allowed
        assert response.headers.get("access-control-allow-credentials") != "true", (
            "VULNERABILITY: CORS allows credentials — attacker JS can steal tokens cross-origin"
        )

        # Wildcard origin is fine without credentials
        # (browsers enforce that * + credentials:true is invalid, so this combo is safe)
        assert response.headers.get("access-control-allow-origin") == "*"


class TestAllowedRedirectURIMerge:
    """Coverage for get_allowed_client_redirect_uris() itself —
    plane_mcp/server.py:39-48 — which widens DEFAULT_ALLOWED_REDIRECT_URIS
    with whatever PLANE_OAUTH_ALLOWED_REDIRECT_URIS supplies. Previously
    untested: TestOAuthRedirectAttack exercised a hand-copied list, not this
    function, so a bad change here (or a misparsed env var) could ship
    without failing any test.
    """

    def test_no_env_override_returns_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", raising=False)
        assert get_allowed_client_redirect_uris() == DEFAULT_ALLOWED_REDIRECT_URIS

    def test_env_var_appends_extra_uris(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "PLANE_OAUTH_ALLOWED_REDIRECT_URIS",
            "https://custom-client.example.com/callback,https://another-client.example.com/*",
        )
        allowed = get_allowed_client_redirect_uris()
        assert allowed[: len(DEFAULT_ALLOWED_REDIRECT_URIS)] == DEFAULT_ALLOWED_REDIRECT_URIS
        assert "https://custom-client.example.com/callback" in allowed
        assert "https://another-client.example.com/*" in allowed

    def test_env_var_ignores_blank_entries_and_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", " https://custom.example.com/cb , , ")
        allowed = get_allowed_client_redirect_uris()
        assert allowed == [*DEFAULT_ALLOWED_REDIRECT_URIS, "https://custom.example.com/cb"]

    def test_env_var_does_not_duplicate_an_existing_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", "https://claude.ai/*")
        allowed = get_allowed_client_redirect_uris()
        assert allowed == DEFAULT_ALLOWED_REDIRECT_URIS

    def test_env_var_does_not_duplicate_within_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "PLANE_OAUTH_ALLOWED_REDIRECT_URIS",
            "https://custom.example.com/cb,https://custom.example.com/cb",
        )
        allowed = get_allowed_client_redirect_uris()
        assert allowed.count("https://custom.example.com/cb") == 1

    def test_env_var_uri_is_accepted_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The merged-in URI isn't just present in the list — the OAuth
        provider actually honors it during a real /authorize flow."""
        monkeypatch.setenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", "https://custom-client.example.com/callback")
        custom_app = _build_app(get_allowed_client_redirect_uris())
        custom_client = TestClient(custom_app, follow_redirects=False)

        reg = _register_client(custom_client, "https://custom-client.example.com/callback")

        response = custom_client.get(
            "/authorize",
            params={
                "client_id": reg["client_id"],
                "redirect_uri": "https://custom-client.example.com/callback",
                "response_type": "code",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )
        assert response.status_code != 400, f"Env-var redirect URI was rejected: {response.text}"
