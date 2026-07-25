"""In-memory protocol tests against the fake cluster. Posts 15 and 16.

`Client(mcp)` connects straight to the server object: no subprocess, no socket,
no cluster. The whole suite runs in a couple of seconds and still goes through
the real protocol machinery, including schema generation, result validation, and
the full elicitation round trip.

Note the shape of every test: the client is opened with `async with` inside the
test body, not handed over by a yield fixture. The client owns an anyio task
group, and a task group has to be exited by the same task that entered it. A
yield fixture tears down in a different task and every test fails with
"Attempted to exit cancel scope in a different task".
"""

from __future__ import annotations

import dataclasses
import inspect as pyinspect
import unicodedata

import pytest
from mcp import Client
from mcp_types import ElicitResult

from k8s_responder import cluster, mcp
from k8s_responder import diagnose as diagnose_module
from k8s_responder import inspect as inspect_module

from conftest import make_deployment, make_replica_set

APPROVE = {"approve": True, "reason": "paged at 02:00"}
REFUSE = {"approve": False}

READ_TOOLS = {
    "list_pods",
    "describe_pod",
    "get_events",
    "get_logs",
    "list_deployments",
    "diagnose_pod",
    "find_crash_loops",
    "get_rollout_history",
}
WRITE_TOOLS = {"restart_pod", "scale_deployment", "rollback_deployment"}


def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always answers the approval prompt the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)


def symbols(text: str) -> list[str]:
    return [ch for ch in text if unicodedata.category(ch) == "So"]


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert {t.name for t in tools} == READ_TOOLS | WRITE_TOOLS
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def test_every_tool_declares_annotations():
    async with Client(mcp) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.read_only_hint is not None


async def test_reads_are_hinted_read_only_and_writes_are_hinted_destructive():
    """The hints have to be honest even though nothing enforces them.

    `read_only_hint` does not stop a tool from writing and does not suppress
    anything in a host: a host is free to show an approval dialog for every
    call, or for none. These flags are metadata for a UI to use as it sees fit.
    What actually stops the read tools writing is that they contain no writing
    code, and what actually stops a remediation is the elicitation below.
    """
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    for name in READ_TOOLS:
        assert tools[name].annotations.read_only_hint is True, name
        assert tools[name].annotations.destructive_hint is False, name

    for name in WRITE_TOOLS:
        assert tools[name].annotations.read_only_hint is False, name
        assert tools[name].annotations.destructive_hint is True, name

    assert tools["scale_deployment"].annotations.idempotent_hint is True
    assert tools["restart_pod"].annotations.idempotent_hint is False


async def test_the_approval_parameter_is_hidden_from_the_model():
    """If approval were an argument, a model could grant its own.

    This is the security test of the whole project. The published schema for
    every writing tool must contain the operational arguments and nothing else.
    """
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    assert set(tools["restart_pod"].input_schema["properties"]) == {
        "pod_name",
        "namespace",
    }
    assert set(tools["scale_deployment"].input_schema["properties"]) == {
        "deployment_name",
        "replicas",
        "namespace",
    }
    assert set(tools["rollback_deployment"].input_schema["properties"]) == {
        "deployment_name",
        "namespace",
        "revision",
    }


def test_the_read_modules_contain_no_mutating_api_calls():
    """The read/write split is structural, and this is the proof.

    Not a naming convention and not a review habit: there is no call in either
    module that could change the cluster, so there is no path from a read tool
    to a mutation regardless of what any annotation claims.
    """
    for module in (inspect_module, diagnose_module):
        source = pyinspect.getsource(module)
        for verb in (
            "delete_namespaced",
            "patch_namespaced",
            "create_namespaced",
            "replace_namespaced",
        ):
            assert verb not in source, f"{module.__name__} can call {verb}"


# --------------------------------------------------------------------------
# The blocking client never runs on the event loop
# --------------------------------------------------------------------------


async def test_kubernetes_calls_run_on_a_worker_thread(fake):
    """The kubernetes client is synchronous. On the loop it freezes the transport.

    A blocking HTTP call made directly from an `async def` tool stops
    everything: no other tool call, no progress notification, no cancellation,
    until the API server answers. `cluster.call` exists to make that impossible,
    and this asserts it rather than trusting it.
    """
    async with Client(mcp) as c:
        await c.call_tool("list_pods", {})
        await c.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})
        await c.call_tool("list_deployments", {})

    assert fake.recorder.calls, "the fake cluster was never called"
    assert "MainThread" not in fake.recorder.threads()


# --------------------------------------------------------------------------
# Reading: post 15
# --------------------------------------------------------------------------


async def test_list_pods_counts_the_unhealthy_ones(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("list_pods", {})

    data = result.structured_content
    assert data["total"] == 4
    assert data["unhealthy"] == 1
    names = {p["name"] for p in data["pods"]}
    assert "checkout-3c-aaa" in names

    crashing = next(p for p in data["pods"] if p["name"] == "checkout-3c-aaa")
    assert crashing["restarts"] == 7
    assert crashing["ready"] == "0/1"
    assert crashing["reason"] == "CrashLoopBackOff"


async def test_list_pods_on_an_empty_namespace_is_not_an_error(empty):
    async with Client(mcp) as c:
        result = await c.call_tool("list_pods", {"namespace": "nothing-here"})
    assert result.structured_content["total"] == 0


async def test_describe_pod_brings_status_containers_and_events_together(fake):
    """One call, three sources. Reading only one of them is how you misdiagnose."""
    async with Client(mcp) as c:
        result = await c.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})

    data = result.structured_content
    assert data["phase"] == "Running"
    container = data["containers"][0]
    assert container["state"] == "waiting"
    assert container["reason"] == "CrashLoopBackOff"
    assert container["last_state"] == "terminated"
    assert container["last_exit_code"] == 1
    assert [e["reason"] for e in data["events"]] == ["BackOff"]


async def test_describe_pod_reports_a_missing_pod_as_a_sentence(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("describe_pod", {"pod_name": "does-not-exist"})
    assert result.is_error
    assert "No pod named" in result.content[0].text


async def test_describe_pod_survives_losing_event_access(fake):
    """Events are a separate RBAC verb. Losing them degrades, it does not fail."""
    fake.events_forbidden = True
    async with Client(mcp) as c:
        result = await c.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})
    assert result.structured_content["events"] == []


async def test_get_events_filters_by_type_and_clamps_the_limit(fake):
    async with Client(mcp) as c:
        warnings = await c.call_tool("get_events", {"event_type": "Warning"})
        everything = await c.call_tool("get_events", {"limit": 9999})

    assert warnings.structured_content["returned"] == 1
    assert warnings.structured_content["warnings"] == 1
    assert everything.structured_content["returned"] == 2


async def test_list_deployments_marks_the_degraded_ones(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("list_deployments", {})

    data = result.structured_content
    by_name = {d["name"]: d for d in data["deployments"]}
    assert by_name["checkout"]["degraded"] is True
    assert by_name["payments"]["degraded"] is False
    assert data["degraded"] == 1


# --------------------------------------------------------------------------
# Log size, the problem this project exists to avoid
# --------------------------------------------------------------------------


async def test_get_logs_returns_the_tail_untouched_when_it_fits(fake):
    async with Client(mcp) as c:
        result = await c.call_tool(
            "get_logs", {"pod_name": "checkout-3c-aaa", "previous": True}
        )

    data = result.structured_content
    assert data["truncated"] is False
    assert data["dropped_bytes"] == 0
    assert "KeyError: 'DB_PASSWORD'" in data["logs"]


async def test_get_logs_caps_bytes_and_says_how_much_it_dropped(fake):
    """5 MB of logs into a context window is the failure being prevented here."""
    fake.logs[("checkout-3c-aaa", "", False)] = "\n".join(
        f"{i:04d} " + "x" * 60 for i in range(500)
    )

    async with Client(mcp) as c:
        result = await c.call_tool(
            "get_logs",
            {"pod_name": "checkout-3c-aaa", "tail_lines": 500, "max_bytes": 2000},
        )

    data = result.structured_content
    assert data["truncated"] is True
    assert data["bytes_returned"] <= 2000
    assert data["dropped_bytes"] > 0
    assert data["dropped_lines"] > 0
    assert "Truncated" in data["note"]

    # The newest end is what survives, because that is where the failure is.
    assert data["logs"].rstrip().endswith("x" * 60)
    assert "0499 " in data["logs"]
    assert "0000 " not in data["logs"]

    # And the cut lands on a line boundary, not mid-line.
    assert data["logs"].splitlines()[0].startswith("0")


async def test_get_logs_clamps_both_bounds_to_the_configured_maximum(fake):
    async with Client(mcp) as c:
        result = await c.call_tool(
            "get_logs",
            {"pod_name": "checkout-3c-aaa", "tail_lines": 100_000, "max_bytes": 10**9},
        )

    cfg = cluster.limits()
    assert result.structured_content["lines_requested"] == cfg.max_tail_lines
    sent = fake.recorder.kwargs_for("read_namespaced_pod_log")[-1]
    assert sent["tail_lines"] == cfg.max_tail_lines


async def test_get_logs_explains_a_missing_previous_instance(fake):
    async with Client(mcp) as c:
        result = await c.call_tool(
            "get_logs", {"pod_name": "checkout-3c-bbb", "previous": True}
        )

    data = result.structured_content
    assert data["logs"] == ""
    assert "never restarted" in data["note"]


async def test_get_logs_on_a_missing_pod_reports_it_without_raising(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("get_logs", {"pod_name": "ghost"})
    assert result.structured_content["logs"] == ""
    assert "No pod named" in result.structured_content["note"]


# --------------------------------------------------------------------------
# Diagnosis over the protocol
# --------------------------------------------------------------------------


async def test_diagnose_pod_correlates_across_the_three_sources(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("diagnose_pod", {"pod_name": "checkout-3c-aaa"})

    data = result.structured_content
    assert data["verdict"] == "crash_loop"
    assert data["healthy"] is False
    assert data["restart_count"] == 7
    sources = {f["source"] for f in data["findings"]}
    assert "events" in sources
    assert "status.phase" in sources


async def test_find_crash_loops_reports_only_the_broken_pods(fake):
    async with Client(mcp) as c:
        result = await c.call_tool("find_crash_loops", {})

    data = result.structured_content
    assert data["scanned"] == 4
    assert data["unhealthy"] == 1
    assert data["diagnoses"][0]["pod"] == "checkout-3c-aaa"
    assert data["verdicts"] == {"crash_loop": 1}


async def test_find_crash_loops_reads_the_namespace_events_once(fake):
    """Two calls for the namespace, not two per pod."""
    async with Client(mcp) as c:
        await c.call_tool("find_crash_loops", {})

    assert fake.recorder.names().count("list_namespaced_event") == 1
    assert fake.recorder.names().count("list_namespaced_pod") == 1


# --------------------------------------------------------------------------
# Remediation: approval, post 16
# --------------------------------------------------------------------------


async def test_declining_changes_nothing(fake):
    async with answering("decline") as c:
        result = await c.call_tool("restart_pod", {"pod_name": "checkout-3c-aaa"})

    data = result.structured_content
    assert data["applied"] is False
    assert data["approved"] is False
    assert "declined" in data["outcome"]
    assert fake.deleted == []


async def test_dismissing_the_prompt_changes_nothing(fake):
    async with answering("cancel") as c:
        result = await c.call_tool("restart_pod", {"pod_name": "checkout-3c-aaa"})

    assert result.structured_content["applied"] is False
    assert "dismissed" in result.structured_content["outcome"]
    assert fake.deleted == []


async def test_answering_no_changes_nothing(fake):
    async with answering("accept", REFUSE) as c:
        result = await c.call_tool("restart_pod", {"pod_name": "checkout-3c-aaa"})

    assert result.structured_content["applied"] is False
    assert "answered no" in result.structured_content["outcome"]
    assert fake.deleted == []


async def test_a_refused_change_still_returns_its_preview(fake):
    """The model should be able to report what would have happened."""
    async with answering("decline") as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 6}
        )

    preview = result.structured_content["preview"]
    assert preview["operation"] == "scale deployment"
    assert preview["change"] == "set spec.replicas to 6"
    assert "deployments/checkout/scale" in preview["api_call"]


async def test_the_question_is_asked_once_and_shows_the_whole_preview(fake):
    """A question that varied between rounds would never match its own digest.

    The SDK pins a recorded answer to a hash of the exact rendered question and
    re-runs the resolver on every round. Anything live in the message, a
    timestamp or the current replica count, re-renders differently, the answer
    looks stale, and the call loops until it hits the round limit. So the
    preview is built from the arguments and nothing else.
    """
    seen: list[str] = []
    async with answering("accept", APPROVE, seen=seen) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )

    assert len(seen) == 1
    question = seen[0]
    assert "scale deployment" in question
    assert "deployment/checkout" in question
    assert "set spec.replicas to 5" in question
    assert "blast radius" in question


async def test_the_same_arguments_render_the_same_question(fake):
    first: list[str] = []
    second: list[str] = []
    async with answering("decline", seen=first) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )
    fake.deployments[0].spec.replicas = 11
    async with answering("decline", seen=second) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )

    assert first == second


# --------------------------------------------------------------------------
# Remediation: blast radius
# --------------------------------------------------------------------------


async def test_scaling_past_the_maximum_is_refused_without_asking_anyone(fake):
    """A change the server was always going to refuse must not reach a human.

    The guard lives in the resolver, which runs before the elicitation is
    rendered, so nobody is invited to approve something impossible.
    """
    seen: list[str] = []
    async with answering("accept", APPROVE, seen=seen) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 500}
        )

    assert result.is_error
    assert "maximum is 20" in result.content[0].text
    assert seen == []
    assert fake.recorder.names() == []


async def test_a_negative_replica_count_is_refused(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": -1}
        )
    assert result.is_error
    assert "cannot be negative" in result.content[0].text


async def test_the_maximum_is_configurable(fake):
    cluster.set_limits(dataclasses.replace(cluster.limits(), max_replicas=2))
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 3}
        )
    assert result.is_error
    assert "maximum is 2" in result.content[0].text


async def test_protected_namespaces_are_refused_for_writes(fake):
    seen: list[str] = []
    async with answering("accept", APPROVE, seen=seen) as c:
        result = await c.call_tool(
            "restart_pod", {"pod_name": "coredns-abc", "namespace": "kube-system"}
        )

    assert result.is_error
    assert "kube-system" in result.content[0].text
    assert seen == []
    assert fake.deleted == []


async def test_protected_namespaces_are_still_readable(fake):
    """Refusing to write to kube-system is not a reason to refuse to look at it."""
    async with Client(mcp) as c:
        result = await c.call_tool("list_pods", {"namespace": "kube-system"})
    assert result.is_error is not True


# --------------------------------------------------------------------------
# Remediation: restart
# --------------------------------------------------------------------------


async def test_an_approved_restart_deletes_the_pod_and_names_its_controller(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool("restart_pod", {"pod_name": "checkout-3c-aaa"})

    data = result.structured_content
    assert data["applied"] is True
    assert data["controller"] == "ReplicaSet/checkout-3c"
    assert fake.deleted == ["default/checkout-3c-aaa"]
    assert "gone" in data["outcome"]
    assert "paged at 02:00" in data["outcome"]


async def test_a_pod_with_no_controller_is_not_deleted(fake):
    """Deleting a bare pod is not a restart, it is a removal.

    The human approved a restart. Doing something strictly more destructive than
    what was described is not covered by that approval, so the tool stops.
    """
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool("restart_pod", {"pod_name": "orphan-pod"})

    data = result.structured_content
    assert data["approved"] is True
    assert data["applied"] is False
    assert "no controlling owner" in data["outcome"]
    assert fake.deleted == []


async def test_restarting_a_pod_that_is_already_gone_says_so(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool("restart_pod", {"pod_name": "ghost"})

    assert result.structured_content["applied"] is False
    assert "No pod named" in result.structured_content["outcome"]


# --------------------------------------------------------------------------
# Remediation: scale
# --------------------------------------------------------------------------


async def test_an_approved_scale_is_applied_and_verified(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 6}
        )

    data = result.structured_content
    assert data["applied"] is True
    assert data["previous_replicas"] == 3
    assert data["requested_replicas"] == 6
    # Read back, not assumed.
    assert data["observed_replicas"] == 6
    assert "read_namespaced_deployment" in fake.recorder.names()


async def test_scaling_to_the_count_it_already_has_is_a_no_op(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 3}
        )

    assert result.structured_content["applied"] is False
    assert "already set to 3" in result.structured_content["outcome"]
    assert "patch_namespaced_deployment_scale" not in fake.recorder.names()


async def test_scaling_to_zero_is_allowed_and_says_what_it_means(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 0}
        )

    data = result.structured_content
    assert data["applied"] is True
    assert "serve no traffic" in data["preview"]["blast_radius"]


async def test_scaling_a_missing_deployment_reports_it(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "nope", "replicas": 2}
        )
    assert result.is_error
    assert "No deployment named" in result.content[0].text


# --------------------------------------------------------------------------
# Remediation: rollback, and the ReplicaSet ownership trap
# --------------------------------------------------------------------------


async def test_rollout_history_only_lists_replicasets_this_deployment_owns(fake):
    """`checkout-canary` selects the same labels. It is not part of this history.

    A label selector matches both deployments' ReplicaSets. Only the controller
    owner reference distinguishes them.
    """
    async with Client(mcp) as c:
        result = await c.call_tool("get_rollout_history", {"deployment_name": "checkout"})

    data = result.structured_content
    assert data["current_revision"] == 3
    assert [r["revision"] for r in data["revisions"]] == [3, 2, 1]
    assert all("canary" not in r["replica_set"] for r in data["revisions"])


async def test_rollback_picks_the_owned_previous_revision(fake):
    """The canary's ReplicaSet is revision 2 and sorts first by label alone.

    Without the ownership filter, "the previous revision" of `checkout` resolves
    to a pod template belonging to `checkout-canary`, and the rollback silently
    overwrites one deployment with another's spec while reporting success.
    """
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "rollback_deployment", {"deployment_name": "checkout"}
        )

    data = result.structured_content
    assert data["applied"] is True
    assert data["from_revision"] == 3
    assert data["to_revision"] == 2
    assert data["restored_images"] == ["registry.example.com/app:1.3.0"]
    assert "canary" not in str(data["restored_images"])


async def test_rolling_back_to_another_deployments_revision_is_refused(fake):
    """Revision 9 exists in the namespace, and belongs to somebody else."""
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "rollback_deployment", {"deployment_name": "checkout", "revision": 9}
        )

    data = result.structured_content
    assert data["applied"] is False
    assert "not in the history" in data["outcome"]
    assert "3, 2, 1" in data["outcome"]
    assert fake.patches == []


async def test_rollback_replaces_the_whole_pod_template(fake):
    """Images alone are not a rollback.

    A bad deploy that added an environment variable and halved a memory limit is
    not fixed by putting the old image back: the pod comes up on the old code
    with the new, broken configuration, the tool reports success, and the
    incident continues. The patch has to replace the entire template.
    """
    async with answering("accept", APPROVE) as c:
        await c.call_tool("rollback_deployment", {"deployment_name": "checkout"})

    patch = fake.json_patch()
    assert isinstance(patch, list)
    assert len(patch) == 1
    assert patch[0]["op"] == "replace"
    assert patch[0]["path"] == "/spec/template"

    sent = fake.recorder.kwargs_for("patch_namespaced_deployment")[-1]
    assert sent["_content_type"] == "application/json-patch+json"

    container = patch[0]["value"]["spec"]["containers"][0]
    assert container["image"] == "registry.example.com/app:1.3.0"

    # The environment variable the bad deploy introduced is gone, not merged.
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env == {"DB_HOST": "db-primary"}
    assert "FEATURE_NEW_CART" not in env

    # And the memory limit goes back with it.
    assert container["resources"]["limits"]["memory"] == "512Mi"


async def test_rollback_strips_the_controller_owned_pod_template_hash(fake):
    """`pod-template-hash` identifies a ReplicaSet; copying it would be a lie."""
    async with answering("accept", APPROVE) as c:
        await c.call_tool("rollback_deployment", {"deployment_name": "checkout"})

    labels = fake.json_patch()[0]["value"]["metadata"]["labels"]
    assert "pod-template-hash" not in labels
    assert labels["app"] == "checkout"


async def test_rollback_records_a_change_cause(fake):
    async with answering("accept", APPROVE) as c:
        await c.call_tool("rollback_deployment", {"deployment_name": "checkout"})

    annotations = fake.json_patch()[0]["value"]["metadata"]["annotations"]
    cause = annotations["kubernetes.io/change-cause"]
    assert "rollback to revision 2" in cause
    assert "paged at 02:00" in cause


async def test_rollback_states_what_it_does_not_restore(fake):
    """The scope travels with the result, not only in the docs.

    Replacing the pod template still leaves the replica count, the deployment's
    own fields, and every ConfigMap and Secret it references untouched. A caller
    who does not know that will close the incident too early.
    """
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "rollback_deployment", {"deployment_name": "checkout"}
        )

    data = result.structured_content
    restored = " ".join(data["restored"])
    not_restored = " ".join(data["not_restored"])

    assert "environment variables" in restored
    assert "resource requests and limits" in restored
    assert "probes" in restored

    assert "spec.replicas" in not_restored
    assert "ConfigMaps" in not_restored
    assert any("spec.replicas was left at 3" in w for w in data["warnings"])


async def test_rollback_from_the_first_revision_has_nowhere_to_go(fake):
    async with answering("accept", APPROVE) as c:
        result = await c.call_tool(
            "rollback_deployment", {"deployment_name": "payments"}
        )

    data = result.structured_content
    assert data["applied"] is False
    assert "no recorded history" in data["outcome"]
    assert fake.patches == []


async def test_rollback_with_only_the_current_revision_has_nowhere_to_go(fake):
    """One revision in the history means there is nothing behind it."""
    billing = make_deployment("billing", revision=1, match_labels={"app": "billing"})
    fake.deployments.append(billing)
    fake.replica_sets.append(
        make_replica_set(
            "billing-1a",
            owner=billing,
            revision=1,
            replicas=1,
            ready=1,
            match_labels={"app": "billing"},
        )
    )

    async with answering("accept", APPROVE) as c:
        result = await c.call_tool("rollback_deployment", {"deployment_name": "billing"})

    data = result.structured_content
    assert data["applied"] is False
    assert "no earlier revision" in data["outcome"]
    assert fake.patches == []


async def test_a_declined_rollback_patches_nothing(fake):
    async with answering("decline") as c:
        result = await c.call_tool(
            "rollback_deployment", {"deployment_name": "checkout"}
        )

    assert result.structured_content["applied"] is False
    assert fake.patches == []
    assert "patch_namespaced_deployment" not in fake.recorder.names()


# --------------------------------------------------------------------------
# House style
# --------------------------------------------------------------------------


async def test_no_tool_description_contains_an_emoji():
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools

    for tool in tools:
        text = f"{tool.name} {tool.title or ''} {tool.description or ''}"
        assert symbols(text) == [], f"{tool.name} has a symbol in its description"


async def test_no_tool_result_contains_an_emoji(fake):
    """These strings are read by a model, not skimmed by a person on a dashboard."""
    async with answering("accept", APPROVE) as c:
        results = [
            await c.call_tool("list_pods", {}),
            await c.call_tool("diagnose_pod", {"pod_name": "checkout-3c-aaa"}),
            await c.call_tool("get_logs", {"pod_name": "checkout-3c-aaa"}),
            await c.call_tool("get_rollout_history", {"deployment_name": "checkout"}),
            await c.call_tool("rollback_deployment", {"deployment_name": "checkout"}),
            await c.call_tool(
                "scale_deployment", {"deployment_name": "checkout", "replicas": 4}
            ),
            await c.call_tool("restart_pod", {"pod_name": "checkout-3c-bbb"}),
        ]

    for result in results:
        for block in result.content:
            assert symbols(getattr(block, "text", "")) == []


@pytest.mark.parametrize("tool_name", sorted(WRITE_TOOLS))
async def test_every_write_tool_requires_an_answer_before_it_acts(tool_name, fake):
    """No writing tool has a path that skips the approval.

    Declining is enough to prove it: the cluster is untouched and the result
    says why.
    """
    arguments = {
        "restart_pod": {"pod_name": "checkout-3c-aaa"},
        "scale_deployment": {"deployment_name": "checkout", "replicas": 5},
        "rollback_deployment": {"deployment_name": "checkout"},
    }[tool_name]

    async with answering("decline") as c:
        result = await c.call_tool(tool_name, arguments)

    assert result.structured_content["applied"] is False
    assert fake.deleted == []
    assert fake.patches == []
