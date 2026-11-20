"""Test helper: existing end-to-end tests act as a user who already confirmed the quoted charge (issue 07)."""

from __future__ import annotations

PAID_COMMANDS = {"generate", "edit", "sfx", "music", "speech", "rig-check", "rig", "animate", "convert"}
CONFIRM = ("--confirmed-quote", "USD:1000")


def with_consent(args: tuple[str, ...] | list[str]) -> list[str]:
    args = list(args)
    if not args or "--quote" in args or "--confirmed-quote" in args or "--ui" in args:
        return args
    paid = args[0] in PAID_COMMANDS or (args[0] == "batch" and len(args) > 1 and args[1] in {"create", "add"})
    return [*args, *CONFIRM] if paid else args


UI_CLOUD_PATHS = ("/api/manager/action", "/api/submit")


def with_ui_consent(url: str, body: dict | None) -> dict | None:
    """Workbench requests: act as a user who confirmed the quote in the page (consent itself is tested in
    tests/test_charge_confirmation.py). Local tools ignore the field."""
    if body is None or not url.endswith(UI_CLOUD_PATHS) or "confirmed_quote" in body:
        return body
    return {**body, "confirmed_quote": "USD:1000"}
