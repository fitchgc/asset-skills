"""Credential context for tests that drive the CLIs against tests/fake_gateway.py.

The Asset Skills accept no directly injected JWT or gateway URL (PR #10 Q6), so tests go the same way
as real use: a fake SageIn site serves asynx-config (pointing at the fake gateway) and a context file
records the fake bridge argv that issues the JWT.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.fake_sagein_server import install_fake_context, run_fake_sagein_server


def bridge_env(test: unittest.TestCase | None, root: Path, gateway_url: str) -> dict[str, str]:
    """Start a fake site for ``gateway_url`` and return the env entry pointing at its context.

    With ``test`` given the site is stopped at test cleanup; otherwise it lives until process exit.
    """
    server, origin, _ = run_fake_sagein_server(gateway_url)
    if test is not None:
        test.addCleanup(server.server_close)
        test.addCleanup(server.shutdown)
    context, _ = install_fake_context(Path(root) / "plaiers-context", origin)
    return {"PLAIERS_SKILL_CONTEXT": str(context)}
