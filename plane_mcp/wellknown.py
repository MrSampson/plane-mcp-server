"""Origin-root well-known routes that are not part of the OAuth metadata.

ChatGPT's app directory verifies that the MCP hostname is ours by fetching a
token from ``/.well-known/openai-apps-challenge`` at the origin root. The token
is issued per submission in the OpenAI developer dashboard, so it arrives as
configuration rather than code, and the route is only registered when one is
set — an unset token must not serve an empty file that passes the fetch and
fails the comparison.

The path is fixed by OpenAI and sits at the origin root regardless of
``MCP_PATH_PREFIX``: the verifier ignores paths on the challenge base URL.
"""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

OPENAI_APPS_CHALLENGE_PATH = "/.well-known/openai-apps-challenge"
OPENAI_APPS_CHALLENGE_ENV = "OPENAI_APPS_CHALLENGE_TOKEN"


def openai_apps_challenge_routes(token: str | None = None) -> list[Route]:
    """Return the domain-verification route when a challenge token is configured.

    ``token`` defaults to ``OPENAI_APPS_CHALLENGE_TOKEN``; whitespace is stripped so
    a trailing newline in a secret store does not break the comparison.
    """
    token = (token if token is not None else os.getenv(OPENAI_APPS_CHALLENGE_ENV, "")).strip()
    if not token:
        return []

    async def challenge(_: Request) -> PlainTextResponse:
        return PlainTextResponse(token, headers={"Cache-Control": "no-store"})

    return [Route(OPENAI_APPS_CHALLENGE_PATH, challenge, methods=["GET"])]
