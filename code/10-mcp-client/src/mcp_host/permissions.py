"""The permission gate.

Post 11, and the reason post 11 exists. A host is a loop around a model plus a
gate, and the gate is the part that matters. Everything else in this project
is plumbing; this file is the security boundary.

**Annotations are hints from the server, and the server is the thing you are
containing.** `readOnlyHint`, `destructiveHint` and the rest are declared by
whoever wrote the tool. Nothing verifies them. A tool named `read_file`
annotated `read_only_hint=True` can delete your home directory, and the
protocol has no opinion about that. So annotations are *input to a decision
the host makes*, never the decision itself. Concretely, in this gate:

- `read_only_hint=True` may buy auto-approval, but only because the host's
  own policy said read-only tools may be auto-approved, and only on servers
  the user chose to connect. Turn `auto_approve_read_only` off and every tool
  prompts, regardless of what any server claims.
- `destructive_hint=True` always prompts and, by default, is never remembered.
  "Allow always" on a tool that deletes things is a decision worth re-taking.
- No annotation at all means prompt. Absence of a claim is not a claim of
  safety.
- The user's own allowlist and denylist outrank every annotation in both
  directions, because those are the host's data, not the server's.

The gate is a policy object plus a prompter, so the same rules can be driven
by a terminal, a GUI dialog, or a test.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence


class Decision(str, Enum):
    """What a human said when asked."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"
    DENY_ALWAYS = "deny_always"


@dataclass(frozen=True)
class PermissionRequest:
    """Everything the gate, and the human, needs to judge one call."""

    tool: str
    server: str
    arguments: dict[str, Any] = field(default_factory=dict)
    annotations: Any = None
    description: str = ""

    @property
    def read_only(self) -> bool:
        return bool(getattr(self.annotations, "read_only_hint", None))

    @property
    def destructive(self) -> bool:
        return bool(getattr(self.annotations, "destructive_hint", None))

    @property
    def key(self) -> str:
        """What a remembered decision is stored against.

        Server plus tool, never the arguments: "always allow reading files"
        is a decision a user can hold in their head, "always allow reading
        /etc/passwd" is a decision they will make once and forget.
        """
        return f"{self.server}.{self.tool}"


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


@dataclass
class PermissionPolicy:
    """The host's rules, written down where a reader can audit them."""

    # Read-only tools may be auto-approved. Off means every tool prompts.
    auto_approve_read_only: bool = True
    # Destructive tools prompt every time, even if previously allowed.
    always_prompt_destructive: bool = True
    # Whether "allow always" is honored for a destructive tool at all.
    remember_destructive: bool = False
    # The user's own lists. These beat any annotation.
    allowlist: frozenset[str] = frozenset()
    denylist: frozenset[str] = frozenset()


class Prompter(Protocol):
    """Asks a human. Anything that returns a `Decision` will do."""

    async def __call__(self, request: PermissionRequest) -> Decision: ...


def always_allow() -> Prompter:
    async def prompter(request: PermissionRequest) -> Decision:
        return Decision.ALLOW_ONCE

    return prompter


def always_deny() -> Prompter:
    async def prompter(request: PermissionRequest) -> Decision:
        return Decision.DENY

    return prompter


def scripted_prompter(
    decisions: Sequence[Decision], *, asked: list[PermissionRequest] | None = None
) -> Prompter:
    """Answers from a list, in order, then denies. For tests."""
    pending = list(decisions)
    log = asked if asked is not None else []

    async def prompter(request: PermissionRequest) -> Decision:
        log.append(request)
        return pending.pop(0) if pending else Decision.DENY

    return prompter


def console_prompter() -> Prompter:
    """Asks at the terminal.

    `asyncio.to_thread` again: this runs inside the tool loop, and a bare
    `input()` here would freeze every other in-flight call while the user
    reads the prompt. ASCII only, for the same cp1252 reason as elsewhere.
    """

    async def prompter(request: PermissionRequest) -> Decision:
        print(f"\n  [permission] {request.server} wants to run {request.tool}")
        if request.description:
            # First line only. A tool docstring can be twenty lines of Args:,
            # and a permission dialog nobody reads is a permission dialog that
            # does not work.
            print(f"    {request.description.strip().splitlines()[0]}")
        print(f"    arguments: {request.arguments}")
        if request.destructive:
            print("    WARNING: the server marks this tool as destructive.")
        try:
            answer = await asyncio.to_thread(
                input, "    allow? [y]es / [a]lways / [n]o / [N]ever: "
            )
        except EOFError:
            # No one there to ask. Deny: an unattended host must not approve
            # by default, and silence is not consent.
            print("    (no input available; denying)")
            return Decision.DENY
        choice = answer.strip()
        if choice == "N":
            return Decision.DENY_ALWAYS
        lowered = choice.lower()
        if lowered in {"a", "always"}:
            return Decision.ALLOW_ALWAYS
        if lowered in {"y", "yes"}:
            return Decision.ALLOW_ONCE
        return Decision.DENY

    return prompter


class PermissionGate:
    """Policy plus prompter plus what the user already said."""

    def __init__(
        self,
        prompter: Prompter | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self.prompter = prompter or console_prompter()
        self.policy = policy or PermissionPolicy()
        self._allowed: set[str] = set()
        self._denied: set[str] = set()

    @property
    def remembered(self) -> dict[str, str]:
        """What the gate is currently carrying. Useful to show a user."""
        return {
            **{k: "allow" for k in sorted(self._allowed)},
            **{k: "deny" for k in sorted(self._denied)},
        }

    def forget(self) -> None:
        self._allowed.clear()
        self._denied.clear()

    async def check(self, request: PermissionRequest) -> Verdict:
        """Decide, prompting only when the rules above require it.

        Read the ordering as a chain of precedence: user data first,
        destructive second, then annotations, then ask.
        """
        key = request.key
        policy = self.policy

        # 1. The user's explicit lists. Deny wins ties.
        if key in policy.denylist or request.tool in policy.denylist:
            return Verdict(False, "on the denylist")
        if key in self._denied:
            return Verdict(False, "denied earlier in this session")
        if key in policy.allowlist or request.tool in policy.allowlist:
            return Verdict(True, "on the allowlist")

        # 2. Destructive. Prompt every time, and by default do not honor a
        #    remembered allow. This is the rule that makes the gate worth
        #    having, so it sits above the annotation shortcuts below.
        if request.destructive and policy.always_prompt_destructive:
            if policy.remember_destructive and key in self._allowed:
                return Verdict(True, "allowed always for this session")
            return await self._ask(request, "the server marks this tool destructive")

        # 3. Remembered allow.
        if key in self._allowed:
            return Verdict(True, "allowed always for this session")

        # 4. The read-only shortcut. Note what this is NOT: it is not trust in
        #    the server. It is the host choosing to spend its own policy budget
        #    on tools that claim to be harmless, on servers the user connected
        #    deliberately.
        if request.read_only and policy.auto_approve_read_only:
            return Verdict(True, "auto-approved: annotated read-only by the server")

        # 5. Everything else, including tools with no annotations at all.
        return await self._ask(request, "no rule covers this tool")

    async def _ask(self, request: PermissionRequest, why: str) -> Verdict:
        decision = await self.prompter(request)
        if decision is Decision.ALLOW_ALWAYS:
            if not request.destructive or self.policy.remember_destructive:
                self._allowed.add(request.key)
            return Verdict(True, f"approved by the user ({why})")
        if decision is Decision.ALLOW_ONCE:
            return Verdict(True, f"approved by the user ({why})")
        if decision is Decision.DENY_ALWAYS:
            # A denial always sticks, even for a destructive tool. Asymmetry is
            # the point: the cheap mistake is to re-ask, the expensive one is
            # to run something the user already refused.
            self._denied.add(request.key)
            return Verdict(False, "denied by the user for this session")
        return Verdict(False, "denied by the user")


def request_for(entry: Any, arguments: dict[str, Any]) -> PermissionRequest:
    """Build a request from a catalog entry."""
    return PermissionRequest(
        tool=entry.tool_name,
        server=entry.server,
        arguments=arguments,
        annotations=entry.annotations,
        description=entry.description,
    )
