"""Regression coverage for the spy's own introspection, not any resource's dispatch.

`SpyClient` binds every call against the real SDK's signature (see `_spyclient.py`).
A real SDK method literally named `list` with a `-> list[...]` return annotation is
exactly the shape that, on Python 3.14 (PEP 649/750 deferred annotations), resolves
`list` against the method being defined rather than the builtin -- see the note in
`_spyclient.py` above `sdk_signature`/`sdk_type_hints`.
"""

from __future__ import annotations

import pytest

from ._spyclient import SpyClient


def test_the_spy_type_checks_an_sdk_method_named_list(spy: SpyClient) -> None:
    with pytest.raises(TypeError, match="params"):
        spy.cycles.list(workspace_slug="acme", project_id="proj-1", params=5)
