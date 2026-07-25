"""The permission gate. Post 11.

The gate is the security boundary, so these tests read as a specification of
it. The one to look at first is
`test_a_lying_read_only_annotation_is_still_only_a_hint`: annotations come
from the server, and the whole design rests on treating them as claims rather
than facts.
"""

from __future__ import annotations

import pytest
from mcp_types import ToolAnnotations

from mcp_host.permissions import (
    Decision,
    PermissionGate,
    PermissionPolicy,
    PermissionRequest,
    always_allow,
    always_deny,
    scripted_prompter,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def request(tool="do_thing", server="demo", annotations=None, **args):
    return PermissionRequest(
        tool=tool, server=server, arguments=args, annotations=annotations
    )


# --------------------------------------------------------------------------
# The annotation shortcuts
# --------------------------------------------------------------------------


async def test_read_only_is_auto_approved_by_default():
    gate = PermissionGate(always_deny())
    verdict = await gate.check(request(annotations=READ_ONLY))

    # The prompter would have denied. It was never called.
    assert verdict.allowed is True
    assert "read-only" in verdict.reason


async def test_read_only_auto_approval_is_a_host_policy_not_a_server_right():
    """Turn the policy off and the same annotation buys nothing.

    This is the test that pins the design down. `read_only_hint` never grants
    anything by itself; it can only satisfy a rule the host chose to have.
    """
    gate = PermissionGate(always_deny(), PermissionPolicy(auto_approve_read_only=False))
    verdict = await gate.check(request(annotations=READ_ONLY))
    assert verdict.allowed is False


async def test_a_lying_read_only_annotation_is_still_only_a_hint():
    """Nothing verifies annotations. A destructive tool can claim both.

    The gate resolves the contradiction the safe way: `destructive_hint` is
    checked before the read-only shortcut, so a tool claiming to be both
    prompts rather than sailing through.
    """
    liar = ToolAnnotations(read_only_hint=True, destructive_hint=True)
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter([Decision.DENY], asked=asked))

    verdict = await gate.check(request(tool="rm_rf", annotations=liar))

    assert verdict.allowed is False
    assert len(asked) == 1


async def test_an_unannotated_tool_prompts():
    """Absence of a claim is not a claim of safety."""
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter([Decision.ALLOW_ONCE], asked=asked))

    verdict = await gate.check(request(annotations=None))

    assert verdict.allowed is True
    assert len(asked) == 1


# --------------------------------------------------------------------------
# Allow once / allow always / deny
# --------------------------------------------------------------------------


async def test_allow_once_does_not_stick():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter([Decision.ALLOW_ONCE, Decision.ALLOW_ONCE], asked=asked)
    )

    assert (await gate.check(request())).allowed is True
    assert (await gate.check(request())).allowed is True
    assert len(asked) == 2


async def test_allow_always_sticks_for_the_session():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter([Decision.ALLOW_ALWAYS], asked=asked))

    assert (await gate.check(request())).allowed is True
    second = await gate.check(request())

    assert second.allowed is True
    assert len(asked) == 1, "the second call should not have prompted"
    assert gate.remembered == {"demo.do_thing": "allow"}


async def test_deny_always_sticks_too():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter([Decision.DENY_ALWAYS], asked=asked))

    assert (await gate.check(request())).allowed is False
    assert (await gate.check(request())).allowed is False
    assert len(asked) == 1


async def test_a_remembered_decision_is_per_tool_not_per_arguments():
    """"Always allow reading files" is a decision a user can hold in their
    head. "Always allow reading /etc/shadow" is one they will forget."""
    gate = PermissionGate(scripted_prompter([Decision.ALLOW_ALWAYS]))

    await gate.check(request(path="/tmp/a"))
    second = await gate.check(request(path="/tmp/b"))

    assert second.allowed is True


async def test_forget_clears_remembered_decisions():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter([Decision.ALLOW_ALWAYS, Decision.DENY], asked=asked)
    )

    await gate.check(request())
    gate.forget()
    verdict = await gate.check(request())

    assert verdict.allowed is False
    assert len(asked) == 2


# --------------------------------------------------------------------------
# Destructive tools
# --------------------------------------------------------------------------


async def test_a_destructive_tool_always_prompts():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter(
            [Decision.ALLOW_ONCE, Decision.ALLOW_ONCE], asked=asked
        )
    )

    await gate.check(request(tool="terminate_process", annotations=DESTRUCTIVE))
    await gate.check(request(tool="terminate_process", annotations=DESTRUCTIVE))

    assert len(asked) == 2


async def test_allow_always_is_not_honoured_for_a_destructive_tool():
    """The user can say "always". The host declines to remember it.

    That is a deliberate asymmetry, and it is the sharp end of the policy:
    approving a deletion once is a decision, approving all future deletions
    from a single dialog is an accident.
    """
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter(
            [Decision.ALLOW_ALWAYS, Decision.ALLOW_ALWAYS], asked=asked
        )
    )

    first = await gate.check(request(tool="rm", annotations=DESTRUCTIVE))
    second = await gate.check(request(tool="rm", annotations=DESTRUCTIVE))

    assert first.allowed is True and second.allowed is True
    assert len(asked) == 2, "always-allow must not be remembered for destructive tools"
    assert gate.remembered == {}


async def test_a_host_can_opt_into_remembering_destructive_approvals():
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter([Decision.ALLOW_ALWAYS], asked=asked),
        PermissionPolicy(remember_destructive=True),
    )

    await gate.check(request(tool="rm", annotations=DESTRUCTIVE))
    second = await gate.check(request(tool="rm", annotations=DESTRUCTIVE))

    assert second.allowed is True
    assert len(asked) == 1


async def test_a_destructive_denial_is_still_remembered():
    """Denials stick even where approvals do not."""
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter([Decision.DENY_ALWAYS], asked=asked))

    await gate.check(request(tool="rm", annotations=DESTRUCTIVE))
    second = await gate.check(request(tool="rm", annotations=DESTRUCTIVE))

    assert second.allowed is False
    assert len(asked) == 1


# --------------------------------------------------------------------------
# The user's own lists outrank everything the server says
# --------------------------------------------------------------------------


async def test_the_denylist_beats_a_read_only_annotation():
    gate = PermissionGate(
        always_allow(), PermissionPolicy(denylist=frozenset({"demo.do_thing"}))
    )
    verdict = await gate.check(request(annotations=READ_ONLY))

    assert verdict.allowed is False
    assert "denylist" in verdict.reason


async def test_the_denylist_beats_the_allowlist():
    gate = PermissionGate(
        always_allow(),
        PermissionPolicy(
            allowlist=frozenset({"demo.do_thing"}),
            denylist=frozenset({"demo.do_thing"}),
        ),
    )
    assert (await gate.check(request())).allowed is False


async def test_the_allowlist_skips_the_prompt_for_a_destructive_tool():
    """An explicit user decision beats the destructive rule, because the list
    is the user's data and the annotation is the server's."""
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter([Decision.DENY], asked=asked),
        PermissionPolicy(allowlist=frozenset({"demo.rm"})),
    )

    verdict = await gate.check(request(tool="rm", annotations=DESTRUCTIVE))

    assert verdict.allowed is True
    assert asked == []


async def test_lists_match_on_the_bare_tool_name_too():
    gate = PermissionGate(always_allow(), PermissionPolicy(denylist=frozenset({"rm"})))
    assert (await gate.check(request(tool="rm", server="other"))).allowed is False


# --------------------------------------------------------------------------
# Namespacing of remembered decisions
# --------------------------------------------------------------------------


async def test_two_servers_with_the_same_tool_name_are_remembered_separately():
    """Approving `search` on the docs server must not approve `search` on the
    filesystem server."""
    asked: list[PermissionRequest] = []
    gate = PermissionGate(
        scripted_prompter([Decision.ALLOW_ALWAYS, Decision.DENY], asked=asked)
    )

    first = await gate.check(request(tool="search", server="docs"))
    second = await gate.check(request(tool="search", server="files"))

    assert first.allowed is True
    assert second.allowed is False
    assert len(asked) == 2
