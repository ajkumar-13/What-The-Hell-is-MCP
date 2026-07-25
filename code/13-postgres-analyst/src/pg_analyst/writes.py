"""The write path: one tool, four gates, one transaction.

Post 14. A write is categorically different from a read, and the difference is
not that it is riskier: it is that it is irreversible by a system that has no
memory of what the previous value was. So the write path adds four things the
read path does not have.

1. **A deployment switch.** `ENABLE_WRITES` is off by default. A server that
   was never meant to write cannot be talked into it.
2. **A separate role.** Writes go through `DATABASE_WRITE_URL`, a second login
   with its own grants. The read path physically cannot reach it.
3. **A human.** The statement is shown to the user and executed only if they
   say yes, through `Resolve` + `Elicit`. The approval parameter is invisible
   to the model, so the model cannot approve its own write.
4. **A transaction with the audit row inside it.** The change and the record of
   the change commit together or not at all.

## Why the confirmation question must be deterministic

The 2026-07-28 revision has no back channel: the server cannot call the client
and wait. It returns "I need this first", the client answers, and the client
calls the same tool again. The SDK matches a recorded answer against a SHA-256
digest of the exact rendered question, so a question that is not a pure
function of the tool's arguments never matches its own answer and the call
loops until `input_required_max_rounds` gives up.

`_confirmation_question()` below derives every character from the validated
plan, which derives from `sql`. No timestamps, no row counts, no live lookups.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

import asyncpg
from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    Resolve,
)
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from .app import log as server_log
from .app import mcp
from .audit import record_operation
from .database import DatabaseNotConfigured, db, writes_enabled
from .security import SecurityError, WritePlan, validate_write_statement

log = logging.getLogger("postgres-analyst.writes")


class WriteApproval(BaseModel):
    """What the human is asked before a statement is committed."""

    confirm: bool = Field(
        description="Confirm that this statement should be committed.",
    )
    reason: str = Field(
        default="",
        description="Optional note recorded alongside the change in the audit log.",
    )


@dataclass
class WriteResult:
    """The outcome of a write attempt.

    `status` is one of:

    - `disabled`  — ENABLE_WRITES is not true on this server.
    - `rejected`  — the validator refused the statement. Nothing ran.
    - `dry_run`   — the statement is valid; nothing ran, nobody was asked.
    - `declined`  — a human was asked and said no, or dismissed the prompt.
    - `committed` — it ran, and it is in the audit log.
    - `failed`    — it ran and rolled back. The failure is in the audit log.
    """

    status: str
    operation: str
    table: str
    executed_sql: str
    affected_rows: int
    audit_id: int
    detail: str


def _outcome(
    plan: WritePlan | None,
    status: str,
    detail: str,
    *,
    affected: int = 0,
    audit_id: int = 0,
) -> WriteResult:
    """Build a `WriteResult`, filling the plan fields in when there is a plan."""
    return WriteResult(
        status=status,
        operation=plan.operation if plan else "",
        table=plan.table if plan else "",
        executed_sql=plan.sql if plan else "",
        affected_rows=affected,
        audit_id=audit_id,
        detail=detail,
    )


def _confirmation_question(plan: WritePlan) -> str:
    """Render the confirmation prompt. Pure function of the plan."""
    return (
        f"Commit this {plan.operation} against {plan.table}?\n\n"
        f"{plan.sql}\n\n"
        "It runs inside a single transaction and is recorded in the audit log."
    )


def _confirm_write(
    sql: str, dry_run: bool
) -> Elicit[WriteApproval] | ElicitationResult[WriteApproval]:
    """Decide what, if anything, to ask the human.

    A resolver may take any of the tool's arguments by name, and the SDK fills
    in defaults the caller omitted, so `dry_run` is always a real bool here.

    Three of the four paths return a stand-in `DeclinedElicitation` instead of
    an `Elicit`, and no question is put to the user at all:

    - writes are switched off, so there is nothing to approve;
    - this is a dry run, so nothing will be executed;
    - the statement is going to be refused anyway, and asking a human to
      approve something the server has already decided to reject teaches them
      to click through prompts without reading them.

    The tool body handles each of those cases before it ever looks at the
    approval, so the stand-in is never mistaken for a real refusal.
    """
    if not writes_enabled() or dry_run:
        return DeclinedElicitation()

    try:
        plan = validate_write_statement(sql)
    except SecurityError:
        return DeclinedElicitation()

    return Elicit(_confirmation_question(plan), WriteApproval)


def affected_rows(command_tag: str) -> int:
    """Rows touched, from PostgreSQL's command tag.

    The tags are `INSERT 0 3`, `UPDATE 5`, and `DELETE 2`: the count is always
    the last field.
    """
    parts = (command_tag or "").split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


async def _record_failure(plan: WritePlan, note: str, error: Exception) -> int:
    """Write the failure row on a fresh write-pool connection.

    This runs after the transaction has already rolled back, which is exactly
    why it cannot reuse that connection: any audit row written inside the
    transaction went away with it. A fresh connection from the **write** pool
    is the only thing that works. The earlier version used the read-only pool
    here, so this insert failed too and the failures were never recorded.

    It swallows its own errors on purpose. Losing the audit row is bad; losing
    the caller's original error message behind a second exception is worse.
    """
    try:
        return await record_operation(
            operation=plan.operation,
            target_table=plan.table,
            sql_text=plan.sql,
            success=False,
            error_message=str(error),
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
        log.error("could not record the failed write in the audit log: %s", exc)
        return 0


@mcp.tool(
    title="Write to the database",
    annotations=ToolAnnotations(
        title="Write to the database",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
async def write_database(
    sql: str,
    approval: Annotated[ElicitationResult[WriteApproval], Resolve(_confirm_write)],
    dry_run: bool = False,
) -> WriteResult:
    """Run one INSERT, UPDATE, or DELETE, after a human confirms it.

    UPDATE and DELETE must carry a WHERE clause. Schema changes, TRUNCATE,
    COPY, GRANT, and MERGE are refused outright. The statement runs inside a
    transaction, and the audit row is written inside that same transaction.

    Call this with `dry_run=True` first. A dry run validates the statement,
    shows exactly what would run, touches no data, and asks nobody anything.

    Args:
        sql: One INSERT, UPDATE, or DELETE statement.
        dry_run: Validate and report without executing. Defaults to False.
    """
    # `approval` never appears in the published input schema: the SDK strips
    # every Resolve-backed parameter. That is what stops the model supplying
    # its own approval and making this whole function decorative.

    # -- gate 1: is this deployment allowed to write at all? ---------------
    if not writes_enabled():
        return _outcome(
            None,
            "disabled",
            "Writes are switched off on this server. Set ENABLE_WRITES=true and "
            "provide DATABASE_WRITE_URL to enable them.",
        )

    # -- gate 2: does the statement survive validation? --------------------
    try:
        plan = validate_write_statement(sql)
    except SecurityError as exc:
        log.info("write refused: %s", exc)
        return _outcome(None, "rejected", f"Refused by the SQL validator: {exc}")

    # -- dry run: report and stop, before anybody is asked anything --------
    if dry_run:
        return _outcome(
            plan,
            "dry_run",
            "Valid. Nothing was executed and nobody was asked to confirm. "
            "Call again with dry_run=False to run it for real.",
        )

    # -- gate 3: did a human say yes? --------------------------------------
    if isinstance(approval, DeclinedElicitation):
        return _outcome(plan, "declined", "The user declined to confirm this write.")
    if isinstance(approval, CancelledElicitation):
        return _outcome(plan, "declined", "The confirmation prompt was dismissed.")

    assert isinstance(approval, AcceptedElicitation)
    if not approval.data.confirm:
        return _outcome(plan, "declined", "The user answered no to the confirmation.")

    note = approval.data.reason.strip()

    if not db.writes_configured:
        return _outcome(
            plan,
            "failed",
            "DATABASE_WRITE_URL is not set, so this server has no write pool. "
            "Nothing was executed.",
        )

    # -- gate 4: one transaction, with the audit row inside it -------------
    try:
        async with db.write_transaction() as conn:
            command_tag = await conn.execute(plan.sql)
            touched = affected_rows(command_tag)
            audit_id = await record_operation(
                operation=plan.operation,
                target_table=plan.table,
                sql_text=plan.sql,
                affected_rows=touched,
                success=True,
                note=note,
                conn=conn,
            )
        # Both statements committed here, together. Had the audit insert
        # raised, the write above it would have rolled back with it, which is
        # the behaviour you want: no unlogged change.
    except (asyncpg.PostgresError, DatabaseNotConfigured, OSError) as exc:
        audit_id = await _record_failure(plan, note, exc)
        server_log.warning("write failed and rolled back: %s", exc)
        return _outcome(
            plan,
            "failed",
            f"Rolled back. PostgreSQL said: {exc}",
            audit_id=audit_id,
        )

    server_log.info(
        "committed %s on %s, %d row(s), audit id %s",
        plan.operation,
        plan.table,
        touched,
        audit_id,
    )
    return _outcome(
        plan,
        "committed",
        f"Committed. {touched} row(s) changed, recorded in the audit log as "
        f"entry {audit_id}.",
        affected=touched,
        audit_id=audit_id,
    )
