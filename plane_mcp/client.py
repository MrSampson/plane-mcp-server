"""Plane client initialization for MCP server."""

import os
from typing import Any, NamedTuple

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import get_logger
from plane import PlaneClient
from plane.config import RetryConfig
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = get_logger(__name__)

# A walk over a large module can outrun an instance's per-key rate limit several times over.
RATE_LIMIT_RETRIES: int = 5
RETRY_POLICY: RetryConfig = RetryConfig(total=RATE_LIMIT_RETRIES)


def _with_retries(client: PlaneClient) -> PlaneClient:
    """Makes every resource of `client` retry a throttled or failed request, honouring `Retry-After`.

    The SDK mounts its retrying adapter only when `Configuration.retry` is set at construction,
    and `PlaneClient` offers no way to set it, so each resource's session is given the adapter here.
    """
    retry: Retry = Retry(
        total=RETRY_POLICY.total,
        backoff_factor=RETRY_POLICY.backoff_factor,
        status_forcelist=RETRY_POLICY.status_forcelist,
        allowed_methods=RETRY_POLICY.allowed_methods,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter: HTTPAdapter = HTTPAdapter(max_retries=retry)
    for resource in vars(client).values():
        session: Any = getattr(resource, "session", None)
        if session is not None:
            session.mount("http://", adapter)
            session.mount("https://", adapter)
    return client


class PlaneClientContext(NamedTuple):
    """Context containing Plane client and workspace information."""

    client: PlaneClient
    workspace_slug: str


def _workspace_slug(token: AccessToken | None) -> str:
    """The slug a connection is bound to: its token's claim, else the environment."""
    if token:
        return token.claims.get("workspace_slug", "")
    return os.getenv("PLANE_WORKSPACE_SLUG", "")


def _connected_via(token: AccessToken | None) -> str:
    """How a connection authenticated: its token's claim, else the environment."""
    if token:
        return token.claims.get("auth_method", "oauth")
    return "environment"


def current_workspace() -> dict[str, Any]:
    """The workspace this connection is bound to, as far as its credentials say."""
    token = get_access_token()
    detail = (token.claims.get("workspace") if token else None) or {}
    return {
        "slug": _workspace_slug(token),
        "id": detail.get("id"),
        "name": detail.get("name"),
        "connected_via": _connected_via(token),
    }


def get_plane_client_context() -> PlaneClientContext:
    """
    Initialize and return a PlaneClient instance with workspace context.

    Authentication is handled by the PlaneOAuthProvider, which supports:
    1. Environment variables (PLANE_API_KEY + PLANE_WORKSPACE_SLUG)
    2. HTTP headers (x-api-key + x-workspace-slug)
    3. OAuth access token

    Environment variables:
    - PLANE_INTERNAL_BASE_URL: Internal URL for Plane API (preferred for server-to-server calls)
    - PLANE_BASE_URL: Base URL for Plane API (fallback, default: https://api.plane.so)

    Returns:
        PlaneClientContext containing configured PlaneClient instance and workspace slug

    Raises:
        ConfigurationError: If access token is not available or workspace slug is missing
    """
    base_url = os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", "https://api.plane.so")

    api_key = os.getenv("PLANE_API_KEY", "")
    access_token = None

    # Get access token from the OAuth provider (which handles all auth methods)
    stored_access_token: AccessToken | None = get_access_token()
    workspace_slug = _workspace_slug(stored_access_token)
    if stored_access_token:
        # Determine authentication method to use appropriate PlaneClient constructor
        auth_method = _connected_via(stored_access_token)
        token = stored_access_token.token

        # For API key auth methods, use api_key parameter; for OAuth, use access_token
        if auth_method in ("api_key_env", "api_key_header"):
            api_key = token
        else:
            access_token = token

    if access_token:
        client = _with_retries(PlaneClient(base_url=base_url, access_token=access_token))
    else:
        client = _with_retries(PlaneClient(base_url=base_url, api_key=api_key))

    return PlaneClientContext(
        client=client,
        workspace_slug=workspace_slug,
    )
