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
