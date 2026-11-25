"""A machine is a named place a dispatch runs, and a branch names one (D112).

Four claims are covered here, each of them a way the feature would be a label
rather than a control if it were missing:

* a policy that names machines refuses another one, at the gate, before a tool
  is considered;
* a policy that names none narrows nothing, which is the asymmetry with `tools`
  and the reason an existing role kept working;
* every dispatch a turn makes carries the branch's machine, so the record and
  the far end both know where it went;
* the prompt says which computer it is — the sentence whose absence was the
  reported symptom ("the model gets confused what computer it's on").
"""

from __future__ import annotations

from fakes import (
    FakeAudit,
    FakeEvents,
    FakeExecution,
    FakeGateway,
    FakeImages,
    FakeRegistry,
    FakeUsage,
    manifest,
)

from neuman_server.decision.harness import Actor, Harness, HarnessConfig
from neuman_server.decision.harness.inbox import Inbox
from neuman_server.decision.harness.ports import Chunk, DispatchOutcome, ModelTarget
from neuman_server.decision.harness.prompt import system_prompt
from neuman_server.decision.policy.compose import EffectivePolicy
from neuman_server.decision.policy.gate import check_machine

DGX = Actor(account_id="acct1", name="alice", machine="dgx", home="/shared/alice")


def _policy(**kwargs) -> EffectivePolicy:
    base = {
        "tools": ("fs_read",),
        "files_read": ("/srv",),
        "files_write": (),
    }
    base.update(kwargs)
    return EffectivePolicy(**base)


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call1"):
    return [
        Chunk("tool_call", {"index": 0, "id": call_id, "function": {"name": name}}),
        Chunk("tool_call", {"index": 0, "function": {"arguments": arguments}}),
        Chunk("finish", {"reason": "tool_calls"}),
    ]


def _harness(*, policy=None, execution=None, audit=None) -> Harness:
    async def policy_for(_name: str):
        return policy or _policy()

    return Harness(
        gateway=FakeGateway(
            [_tool_call("fs_read", '{"path": "/srv/x"}'), [Chunk("finish", {})]]
        ),
        execution=execution or FakeExecution(),
        registry=FakeRegistry([manifest("fs_read", needs={"read"})]),
        audit_repository=audit or FakeAudit(),
        event_repository=FakeEvents(),
        usage=FakeUsage(),
        images=FakeImages(),
        policy_for=policy_for,
        resolve_model=_resolve,
        config=HarnessConfig(approval_timeout_s=0.05),
    )


async def _resolve(name: str):
    return ModelTarget(name=name, provider_model=name, context_tokens=100_000)


# -- the gate ---------------------------------------------------------------


def test_a_policy_with_no_machines_narrows_nothing():
    """The asymmetry with `tools`, and the reason an existing role kept working.

    A tool is something the appliance holds and hands out, so an empty list
    grants none. A machine is a place this account's own client logged in from,
    so enrolment is already the grant: naming none confines nobody, and refusing
    would say "you may not use the computer you are typing on".
    """

    assert check_machine("ernest", _policy()).allowed
    assert check_machine("dgx", _policy(machines=())).allowed


def test_a_role_that_names_machines_refuses_another():
    decision = check_machine("dgx", _policy(machines=("laptop",)))

    assert not decision.allowed
    assert decision.reason == "machine_not_granted"
    # The sentence names what was granted, because the remedy is an
    # administrator's and this is the only place it is stated.
    assert "laptop" in decision.detail
    assert "dgx" in decision.detail


def test_a_named_machine_is_allowed_when_it_is_granted():
    assert check_machine("dgx", _policy(machines=("laptop", "dgx"))).allowed


def test_a_branch_with_no_machine_yet_is_not_refused():
    """Rows written before machines existed adopt one on their next turn.

    Refusing them would strand every conversation predating the column, and the
    turn that runs on one is what pins it — so the check lets it through and the
    harness records where it actually went.
    """

    assert check_machine(None, _policy(machines=("laptop",))).allowed


# -- the dispatch -----------------------------------------------------------


async def test_every_dispatch_carries_the_machines_the_turn_runs_on():
    """The record and the far end both need it, and neither can infer it.

    A fake that ignored the field would let the harness stop sending it with
    every test still passing — the N38 shape, one layer up.
    """

    execution = FakeExecution(DispatchOutcome(exit_code=0, stdout="ok"))
    harness = _harness(execution=execution)

    await harness.run_turn(
        branch_id="s1", actor=DGX, message="hi", model="m", inbox=Inbox("s1")
    )

    reads = [d for d in execution.dispatches if d["tool"] == "fs_read"]
    assert reads, "the tool call never reached the client"
    assert reads[0]["machine"] == "dgx"


async def test_a_dispatch_to_an_ungranted_machine_is_denied_before_the_tool():
    """Refused at the gate, recorded, and never sent anywhere.

    The same shape as a tool the policy does not list: the refusal is in the
    chain whatever caused the model to ask, and the model is told which machine
    it may not use rather than left to retry.
    """

    execution = FakeExecution(DispatchOutcome(exit_code=0, stdout="ok"))
    audit = FakeAudit()
    harness = _harness(execution=execution, audit=audit, policy=_policy(machines=("ernest",)))

    await harness.run_turn(
        branch_id="s1", actor=DGX, message="hi", model="m", inbox=Inbox("s1")
    )

    assert not [d for d in execution.dispatches if d["tool"] == "fs_read"]
    denied = [row for row in audit.rows if row.action == "tool.denied"]
    assert denied, "the refusal was not recorded"
    assert denied[0].payload["reason"] == "machine_not_granted"
    assert denied[0].payload["machine"] == "dgx"


async def test_the_record_names_the_machine_a_dispatch_ran_on():
    """`SPEC.md` §5 promised this before there was anything to name."""

    execution = FakeExecution(DispatchOutcome(exit_code=0, stdout="ok"))
    audit = FakeAudit()
    harness = _harness(execution=execution, audit=audit)

    await harness.run_turn(
        branch_id="s1", actor=DGX, message="hi", model="m", inbox=Inbox("s1")
    )

    dispatched = [row for row in audit.rows if row.action == "tool.dispatch"]
    assert dispatched, "the dispatch was not recorded"
    assert dispatched[0].payload["machine"] == "dgx"


# -- the prompt -------------------------------------------------------------


def test_the_prompt_names_the_computer_and_says_paths_mean_it():
    """The reported symptom was a model that could not tell which computer it
    was on. The sentence has to say the name *and* what a path then means."""

    text = system_prompt(_policy(), user="alice", tools=["fs_read"], machine="dgx")

    assert "on `dgx`" in text
    assert "Every path you use names a file there" in text


def test_the_prompt_says_which_home_it_means_with_no_machine():
    """A turn with no client connected can still answer, and it must not claim a
    computer it does not have."""

    text = system_prompt(_policy(), user="alice", tools=[], machine=None)

    assert "on their machine." in text


def test_the_prompt_states_what_is_installed_on_that_computer():
    """`hello` advertises Neuman tools and says nothing about `PATH`, so without
    this a missing toolkit is discovered one refused turn at a time."""

    text = system_prompt(
        _policy(),
        user="alice",
        tools=["fs_read"],
        machine="dgx",
        machine_notes="FreeSurfer 7.4, CUDA 12",
    )

    assert "FreeSurfer 7.4, CUDA 12" in text
