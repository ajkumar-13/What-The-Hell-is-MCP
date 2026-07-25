"""Crash-loop detection against synthetic pods. Post 15.

`analyse()` is a pure function of a pod object and a list of events, so every
case below is a shape of failure written out in full and handed straight to it.
No cluster, no network, no server, no event loop. That is the point of keeping
the analysis separate from the tools that fetch its inputs: the interesting
logic is the correlation, and correlation is testable without any of the rest.

Each test names the signature it is pinning down, because the failure modes look
alike from a distance and the difference between them is what makes a diagnosis
worth reading.
"""

from __future__ import annotations

import unicodedata

from k8s_responder.diagnose import analyse
from k8s_responder.inspect import ClusterEvent

from conftest import (
    container_status,
    make_pod,
    running,
    terminated,
    waiting,
)


def event(reason: str, message: str = "something happened", type_: str = "Warning"):
    return ClusterEvent(
        type=type_,
        reason=reason,
        object="Pod/x",
        message=message,
        count=1,
        first_seen="2026-07-26T11:00:00+00:00",
        last_seen="2026-07-26T11:58:00+00:00",
    )


# --------------------------------------------------------------------------
# The healthy case, so the rest means something
# --------------------------------------------------------------------------


def test_a_running_ready_pod_is_healthy():
    pod = make_pod("web-1", statuses=[container_status(ready=True, restarts=0)])
    result = analyse(pod, [])
    assert result.healthy is True
    assert result.verdict == "healthy"
    assert result.likely_cause == ""
    assert result.next_steps == []


def test_a_completed_pod_is_not_a_failure():
    pod = make_pod(
        "job-1",
        phase="Succeeded",
        statuses=[container_status(ready=False, state=terminated(0, "Completed"))],
    )
    assert analyse(pod, []).healthy is True


# --------------------------------------------------------------------------
# Crash loops
# --------------------------------------------------------------------------


def test_crashloopbackoff_is_a_crash_loop():
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=9,
                state=waiting("CrashLoopBackOff", "back-off 5m0s"),
                last_state=terminated(1, "Error"),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "crash_loop"
    assert result.confidence == "high"
    assert result.restart_count == 9


def test_the_exit_code_is_translated_not_just_repeated():
    """A number is not a diagnosis. 127 means the entrypoint is not there."""
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=5,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(127, "Error"),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "crash_loop"
    assert "127" in result.likely_cause
    assert "not found in the image" in result.likely_cause


def test_exit_code_zero_is_still_a_crash_loop():
    """A container whose main process finishes cleanly restarts forever."""
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=12,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(0, "Completed"),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "crash_loop"
    assert "restartPolicy Always" in result.likely_cause


def test_a_crash_loop_between_backoffs_is_still_caught():
    """The container is `running` right now, which is the trap.

    Between back-off windows the container really is up, so a check that only
    looks for the CrashLoopBackOff waiting reason reports the pod as fine. The
    restart count and the previous exit code say otherwise.
    """
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=6,
                state=running(),
                last_state=terminated(1, "Error"),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "crash_loop"
    assert result.confidence == "medium"


def test_an_unknown_exit_code_says_so_rather_than_guessing():
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=4,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(42, "Error"),
            )
        ],
    )
    result = analyse(pod, [])
    assert "application-defined" in result.likely_cause


# --------------------------------------------------------------------------
# Signatures that look like crash loops and are not
# --------------------------------------------------------------------------


def test_oom_beats_crashloop():
    """A pod killed for memory reports BOTH. Reporting the loop is useless.

    OOMKilled is the actionable half: no amount of restarting fixes a memory
    limit that is too low, and a report that says "CrashLoopBackOff" sends the
    reader to the application logs instead of to the resource limits.
    """
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=4,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(137, "OOMKilled"),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "oom_killed"
    assert "memory" in result.likely_cause.lower()


def test_image_pull_failure_is_not_a_crash_loop():
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=0,
                state=waiting("ImagePullBackOff", "Back-off pulling image"),
            )
        ],
        phase="Pending",
    )
    result = analyse(pod, [])
    assert result.verdict == "image_pull_failure"
    assert "registry" in result.likely_cause


def test_a_missing_configmap_is_a_config_error():
    pod = make_pod(
        "web-1",
        phase="Pending",
        statuses=[
            container_status(
                ready=False,
                state=waiting(
                    "CreateContainerConfigError",
                    'configmap "checkout-config" not found',
                ),
            )
        ],
    )
    result = analyse(pod, [])
    assert result.verdict == "config_error"
    assert "checkout-config" in result.likely_cause


def test_pending_with_failedscheduling_is_unschedulable():
    pod = make_pod("web-1", phase="Pending", statuses=[])
    result = analyse(
        pod,
        [event("FailedScheduling", "0/3 nodes are available: 3 Insufficient cpu.")],
    )
    assert result.verdict == "unschedulable"
    assert "Insufficient cpu" in result.likely_cause


def test_pending_without_an_event_is_just_pending():
    """Not every Pending pod is broken. Most of them are downloading an image."""
    pod = make_pod("web-1", phase="Pending", statuses=[])
    result = analyse(pod, [])
    assert result.verdict == "pending"
    assert result.confidence == "low"


def test_a_failing_probe_is_reported_as_a_probe_failure():
    pod = make_pod(
        "web-1",
        statuses=[container_status(ready=False, restarts=1, state=running())],
    )
    result = analyse(
        pod, [event("Unhealthy", "Readiness probe failed: HTTP probe failed with 503")]
    )
    assert result.verdict == "probe_failure"
    assert "503" in result.likely_cause


def test_a_ready_pod_with_an_old_unhealthy_event_is_not_a_probe_failure():
    pod = make_pod("web-1", statuses=[container_status(ready=True)])
    result = analyse(pod, [event("Unhealthy", "Readiness probe failed")])
    assert result.healthy is True


def test_a_pod_that_is_up_but_has_restarted_a_lot_is_flagged():
    pod = make_pod("web-1", statuses=[container_status(ready=True, restarts=11)])
    result = analyse(pod, [])
    assert result.verdict == "restarting"
    assert result.restart_count == 11


def test_a_terminating_pod_is_not_reported_as_a_failure():
    pod = make_pod("web-1", deleting=True, statuses=[container_status(ready=True)])
    result = analyse(pod, [])
    assert result.verdict == "terminating"


def test_the_failed_phase_is_reported_with_its_reason():
    pod = make_pod("web-1", phase="Failed", statuses=[], reason="Evicted")
    result = analyse(pod, [])
    assert result.verdict == "failed"
    assert "Evicted" in result.likely_cause


def test_an_unrecognised_failure_says_it_does_not_know():
    """The honest answer beats a confident wrong one."""
    pod = make_pod(
        "web-1",
        statuses=[container_status(ready=False, restarts=0, state=running())],
    )
    result = analyse(pod, [])
    assert result.verdict == "unknown"
    assert result.confidence == "low"
    assert "Not determined" in result.likely_cause


# --------------------------------------------------------------------------
# Findings: a diagnosis you can check
# --------------------------------------------------------------------------


def test_every_finding_names_the_field_it_came_from():
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=9,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(1, "Error"),
            )
        ],
    )
    result = analyse(pod, [event("BackOff", "Back-off restarting failed container")])

    sources = {f.source for f in result.findings}
    assert "status.phase" in sources
    assert "status.containerStatuses[app].state.waiting" in sources
    assert "status.containerStatuses[app].lastState.terminated" in sources
    assert "status.containerStatuses[app].restartCount" in sources
    assert "events" in sources


def test_only_warning_events_become_findings():
    pod = make_pod("web-1", statuses=[container_status(ready=True)])
    result = analyse(
        pod,
        [
            event("Pulled", "Container image already present", type_="Normal"),
            event("BackOff", "Back-off restarting"),
        ],
    )
    signals = [f.signal for f in result.findings if f.source == "events"]
    assert signals == ["BackOff"]


def test_next_steps_point_at_the_previous_container_log():
    """For a crash loop the current instance has usually logged nothing."""
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=5,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(1),
            )
        ],
    )
    steps = " ".join(analyse(pod, []).next_steps)
    assert "previous=True" in steps


def test_init_containers_are_analysed_too():
    """A pod stuck in its init container looks healthy if you skip them."""
    pod = make_pod("web-1", phase="Pending", statuses=[])
    pod.status.init_container_statuses = [
        container_status(
            name="wait-for-db",
            ready=False,
            restarts=8,
            state=waiting("CrashLoopBackOff"),
            last_state=terminated(1),
        )
    ]
    result = analyse(pod, [])
    assert result.verdict == "crash_loop"
    assert "wait-for-db" in result.summary


def test_a_pod_with_no_status_at_all_does_not_explode():
    pod = make_pod("web-1", statuses=[])
    pod.status = None
    result = analyse(pod, [])
    assert result.pod == "web-1"


# --------------------------------------------------------------------------
# House style
# --------------------------------------------------------------------------


def test_no_diagnosis_text_contains_an_emoji():
    """Tool output is data a model reads, not a status page a person skims."""
    pod = make_pod(
        "web-1",
        statuses=[
            container_status(
                ready=False,
                restarts=9,
                state=waiting("CrashLoopBackOff"),
                last_state=terminated(137, "OOMKilled"),
            )
        ],
    )
    result = analyse(pod, [event("BackOff", "Back-off restarting failed container")])

    text = " ".join(
        [result.summary, result.likely_cause, *result.next_steps]
        + [f"{f.source} {f.signal} {f.detail}" for f in result.findings]
    )
    symbols = [ch for ch in text if unicodedata.category(ch) == "So"]
    assert symbols == []
