"""Per-request charge confirmation before any paid Generation Task submission (issue 07).

A JWT, the ``skill_jwt`` scope, or installing the skill never count as payment consent. Before a new
paid submission (a single task or a batch), the CLI asks the Asset Gateway for a quote
(``POST /v1/tasks/quote``) and stops with ``confirmation_required``, showing the cost and the
remaining budget. The Agent shows that to the user. Only an explicit confirmation of this request,
re-run with ``--confirmed-quote <amount|quote_id>``, submits. Before submitting, the CLI asks for a
fresh quote. A higher price, another currency or an unknown quote ID asks again; it never submits
silently. A quote of 0 needs no confirmation.

If no quote can be obtained, the result is ``quote_unavailable`` and nothing is submitted. The user
can still go ahead without a quote (``--confirm-without-quote``); that decision is recorded in the
request log and on the local task.

Resuming or recovering an already-submitted task (same idempotency key) is not a new charge decision
and never passes through here.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import AsxError

QUOTE_FAILURE_PASSTHROUGH = {"need_login", "invalid_partner_token", "quota_exhausted", "budget_exceeded"}
_AMOUNT_RE = re.compile(r"^\s*(?:([A-Za-z]{3})\s*[: ]\s*)?(\d+(?:\.\d+)?)\s*(?:([A-Za-z]{3}))?\s*$")


def add_charge_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--confirmed-quote",
        metavar="AMOUNT|QUOTE_ID",
        help="用户已确认本次费用（报价金额，如 0.12 或 USD:0.12，或报价 ID）；未提供时只报价、不提交",
    )
    parser.add_argument(
        "--confirm-without-quote",
        action="store_true",
        help="报价不可用时，用户明确同意在没有报价的情况下提交（会记入请求日志）",
    )


def _amount(quote: dict[str, Any]) -> Decimal | None:
    for key in ("charge_amount", "estimated_amount", "amount"):
        value = quote.get(key)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _quote_id(quote: dict[str, Any]) -> str | None:
    value = quote.get("quote_id") or quote.get("id")
    return str(value) if value else None


def _budget(client: Any) -> dict[str, Any] | None:
    identity_fn = getattr(client, "partner_identity", None)
    if not callable(identity_fn):
        return None
    try:
        identity, _ = identity_fn()
    except AsxError:
        return None
    budget = identity.get("budget") if isinstance(identity, dict) else None
    return budget if isinstance(budget, dict) else None


def record_decision(record: dict[str, Any]) -> None:
    """Append the charge decision to the request log (same JSONL file). Never raises."""
    try:
        from .request_log import _lock, log_path

        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": "charge_consent", **record},
                          ensure_ascii=False) + "\n"
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _confirm_args(total: Decimal, currency: str | None, quote_id: str | None) -> list[str]:
    value = quote_id or (f"{currency}:{total}" if currency else str(total))
    return ["--confirmed-quote", value]


def require_charge_consent(
    client: Any,
    *,
    task_type: str,
    body: dict[str, Any],
    model: str,
    args: argparse.Namespace,
    units: int = 1,
) -> dict[str, Any]:
    """Return the consent record for this submission, or raise ``confirmation_required`` / ``quote_unavailable``."""
    confirmed = getattr(args, "confirmed_quote", None)
    without_quote = bool(getattr(args, "confirm_without_quote", False))
    quote_body = {k: v for k, v in body.items() if not k.startswith("_asx_")}
    try:
        quote, quote_request_id = client.quote(task_type, quote_body)
    except AsxError as exc:
        if exc.code in QUOTE_FAILURE_PASSTHROUGH:
            raise
        if without_quote:
            record = {"task_type": task_type, "model": model, "units": units, "quote": None,
                      "confirmed_without_quote": True, "quote_error": exc.code}
            record_decision(record)
            return record
        raise AsxError(
            "无法获取本次费用报价，未提交任何任务。请稍后重试；如用户明确同意在没有报价的情况下提交，再加 --confirm-without-quote。",
            code="quote_unavailable",
            exit_code=2,
            details={"task_type": task_type, "model": model, "units": units, "quote_error": exc.code,
                     "submitted": False, "confirm_args": ["--confirm-without-quote"]},
        ) from exc

    unit_amount = _amount(quote) if isinstance(quote, dict) else None
    if unit_amount is None:
        if without_quote:
            record = {"task_type": task_type, "model": model, "units": units, "quote": quote,
                      "confirmed_without_quote": True, "quote_error": "quote_without_amount"}
            record_decision(record)
            return record
        raise AsxError(
            "报价结果没有金额，未提交任何任务。如用户明确同意在没有报价的情况下提交，再加 --confirm-without-quote。",
            code="quote_unavailable",
            exit_code=2,
            details={"task_type": task_type, "model": model, "quote": quote, "submitted": False,
                     "confirm_args": ["--confirm-without-quote"]},
        )
    currency = str(quote.get("currency")) if quote.get("currency") else None
    total = unit_amount * units
    quote_id = _quote_id(quote)
    summary = {"task_type": task_type, "model": model, "units": units, "currency": currency,
               "unit_amount": str(unit_amount), "total_amount": str(total), "quote": quote,
               "quote_request_id": quote_request_id}
    if total == 0:
        return {**summary, "confirmation": "not_required"}

    reason = "not_confirmed"
    if confirmed:
        if quote_id and confirmed.strip() == quote_id:
            record = {**summary, "confirmed": confirmed, "confirmation": "quote_id"}
            record_decision(record)
            return record
        match = _AMOUNT_RE.match(confirmed)
        if match:
            confirmed_currency = (match.group(1) or match.group(3) or "").upper() or None
            confirmed_amount = Decimal(match.group(2))
            if confirmed_currency and currency and confirmed_currency != currency.upper():
                reason = "currency_changed"
            elif total > confirmed_amount:
                reason = "price_changed"
            else:
                record = {**summary, "confirmed": confirmed, "confirmation": "amount"}
                record_decision(record)
                return record
        else:
            reason = "quote_mismatch"

    budget = _budget(client)
    message = {
        "not_confirmed": "这是付费请求，需要用户确认本次费用后才会提交。",
        "price_changed": "报价已变化（高于用户确认的金额），未提交；请把新报价告知用户并重新确认。",
        "currency_changed": "报价币种与用户确认的不同，未提交；请重新确认。",
        "quote_mismatch": "确认的报价已过期或不匹配，未提交；请把新报价告知用户并重新确认。",
    }[reason]
    raise AsxError(
        f"{message}本次 {units} 个任务预计 {total} {currency or ''}".rstrip() + "。授权或安装 Skill 不代表同意付费。",
        code="confirmation_required",
        exit_code=2,
        details={**summary, "reason": reason, "budget": budget, "submitted": False,
                 "confirm_args": _confirm_args(total, currency, quote_id)},
    )
