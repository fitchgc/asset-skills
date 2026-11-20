# ADR-0008: Task ledger, idempotency and charge confirmations belong to one identity

Status: accepted (dev only), 2026-11-20. Amends ADR-0003 and ADR-0006: the ledger is shared across the
skills of one identity, no longer across every installation of one OS user.

## Context

ADR-0003 put every task of one OS user into one ledger. PR tanqunzhao/Plaiers#10 then gave each
installed folder its own identity (site + Agent + account), but the ledger stayed shared and recovery
re-sent any unsubmitted request it found. Reproduced by the owner: account A's confirmed request failed
with a 503; account B, from its own installation, ran `task recover`; the gateway received A's prompt
under B's JWT, with no quote and no confirmation by B.

## Decision

- **Identity.** Site origin + Agent + account. The account is the pinned `--account-id`, otherwise the
  `sub` of the JWT the bridge issues for the context. A session keeps its account for its lifetime.
- **One ledger per identity.** `state_path()` is `<state>/asx/identities/<sha256(origin, agent,
  account)[:32]>/state.db`. All skills installed for one identity still share it, so cross-skill
  `--from-task` and lineage work as before. The request log moves with it.
- **Ownership is recorded and checked, not implied by the path.** The ledger stores its owner
  (`ledger_owner`). Every open through `connect_db()` compares it with the current identity:
  another owner is `ledger_owner_mismatch`; no owner but existing cloud tasks or batches is
  `ledger_owner_unknown`; an empty unowned ledger is claimed atomically by the first identity.
  `ASYNX_STATE_PATH` only chooses the file; it does not bypass the check.
- **Charge confirmation is a ledger record.** `charge_consents` binds an idempotency key to the
  confirming identity, the gateway and a digest of the task type and request body. `submit_confirmed`
  is the only caller of `client.submit` (first submission, `task recover`, batch items). Without a
  matching record nothing is sent (`charge_consent_missing` / `charge_consent_mismatch`). Recovery reuses
  the original idempotency key and does not quote again.
- **Gateway.** A task is continued only on the gateway it was submitted to (`tasks.base_url`), else
  `task_gateway_mismatch`. A batch is checked as a whole before anything is written: a refused
  `batch cancel` / `poll` / `wait` / `status --refresh` / `add` leaves the batch, its items and its tasks
  as they were and sends nothing to either gateway (PR #10 P2, 2026-11-20). A batch is bound to its
  gateway when it is created (`batches.gateway`), so one that paused before its first submission (pending
  items and confirmations, no task rows yet) is refused too; batches from earlier releases without that
  column are bound through their items' charge confirmations (`charge_consents.gateway`) and tasks.
- **Unowned records from earlier releases** stay in the old per-user file. No identity reads them.
  `ledger migrate --confirm-ownership` copies that file into the current identity's ledger when it is
  still empty, and leaves the source untouched. Copied tasks have no charge confirmation, so one that
  never reached the gateway is not resubmitted.
- **Without a resolvable identity** (no context, or the bridge fails) only the unbound per-user ledger
  is used, for local processing. Cloud commands fail with `need_login` as before.

## Consequences

- With an unpinned account, commands that open the ledger run the bridge once per process to learn the
  account; the bridge serves its cached JWT, so this normally needs no network.
- Local processing done while the account cannot be resolved is recorded in the unbound ledger and does
  not appear in the identity's lineage.
- Merging an old ledger into one that already has records is not supported; migration is refused
  (`ledger_not_empty`).
- The shared Pillow runtime and the model catalog cache hold no identity or billing state and stay shared.
