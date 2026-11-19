"""Stand-in for plaiers-community get-skill-jwt.mjs used by the Asset Skill tests.

Mirrors the bridge contract the skills rely on: same flags, stdout carries only the JWT, errors go to
stderr with exit 1, the token is keyed to one site + Agent + account + PAT and its `sub` must match the
bound account. Identity selection:
  * env-token mode: SAGEIN_COMMUNITY_TOKEN + mandatory --account-id (no binding file);
  * binding mode:   <config-dir>/bindings/<agent-id>.json {account_id, access_token, cache};
    --account-id, when given, must equal the bound account.
Every invocation is appended to <config-dir>/bridge-calls.jsonl for assertions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

FLAGS = {"--agent-id", "--origin", "--config-dir", "--credential-file", "--account-id"}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def subject(token: str) -> str | None:
    try:
        seg = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]
    except Exception:
        return None


def main() -> None:
    argv = sys.argv[1:]
    args: dict[str, str] = {}
    for key, value in zip(argv[::2], argv[1::2]):
        if key not in FLAGS or key in args:
            fail("Invalid credential options.")
        args[key] = value
    config_dir = Path(args["--config-dir"])
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "bridge-calls.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": sys.argv, "cwd": os.getcwd()}) + "\n")
    origin, agent = args["--origin"], args["--agent-id"]
    pinned = args.get("--account-id")
    binding_file = config_dir / "bindings" / f"{agent}.json"
    env_token = os.environ.get("SAGEIN_COMMUNITY_TOKEN")
    if binding_file.is_file():
        binding = json.loads(binding_file.read_text(encoding="utf-8"))
        account, pat, use_cache = binding["account_id"], binding["access_token"], bool(binding.get("cache"))
        if pinned and pinned != account:
            fail("Agent/site/account binding mismatch. No alternate credential will be used.")
        if env_token and env_token != pat:
            fail("Credential/account conflict. Environment tokens cannot override an Agent binding.")
    elif env_token and pinned:
        account, pat, use_cache = pinned, env_token, False
    else:
        fail("No binding for this Agent and site. Run device-login with the same --agent-id.")
    key = hashlib.sha256(json.dumps([origin, agent, account, hashlib.sha256(pat.encode()).hexdigest()]).encode()).hexdigest()
    cache = config_dir / "jwt" / f"{key}.json"
    if use_cache and cache.is_file():
        hit = json.loads(cache.read_text(encoding="utf-8"))
        if hit["expiresAt"] - time.time() * 1000 > 600_000 and subject(hit["token"]) == account:
            print(hit["token"])
            return
    req = Request(origin + "/api/agent/jwt", data=b"", method="POST",
                  headers={"Accept": "application/json", "Authorization": "Bearer " + pat})
    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read())["data"]
    except HTTPError as exc:
        if exc.code == 403:
            fail("This authorization lacks skill_jwt; ask the user to approve it explicitly.")
        fail(f"JWT request failed (HTTP {exc.code}).")
    if subject(data["token"]) != account:
        fail("Issued JWT does not match the bound account. Operation blocked.")
    if use_cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"token": data["token"], "expiresAt": data["expiresAt"]}), encoding="utf-8")
    print(data["token"])


if __name__ == "__main__":
    main()
