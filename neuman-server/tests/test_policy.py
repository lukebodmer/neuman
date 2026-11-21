"""Policy composition, the capability gate, and the working-directory check."""

from __future__ import annotations

import json

import pytest
from fakes import FakeDirectory, FakeGrants, FakeRegistry, manifest

from neuman_server.decision.policy import compose, gate, policy_hash
from neuman_server.decision.policy.compose import EffectivePolicy
from neuman_server.decision.policy.fragments import FragmentError, PolicyFragment
from neuman_server.decision.policy.roles import RoleError, rename_in_baseline
from neuman_server.decision.policy.source import (
    PolicySource,
    layers_and_roles,
    layers_for,
    roles_in_force,
)

# -- fragments --------------------------------------------------------------


def test_a_list_field_is_a_list():
    """The old `{extra,only,remove}` form fails closed rather than meaning anything."""

    with pytest.raises(FragmentError, match="expected a list of strings"):
        PolicyFragment.parse({"tools": {"remove": ["shell"]}}, replaces=True)


def test_a_baseline_list_adds_and_an_overlay_list_replaces():
    """D87: an account's override is the final say, whatever the roles give."""

    policy = compose(
        [
            ("defaults", PolicyFragment.parse({"tools": ["fs_read"]})),
            ("role:eng", PolicyFragment.parse({"tools": ["fs_read", "shell"]})),
        ]
    )
    assert policy.tools == ("fs_read", "shell")

    policy = compose(
        [
            ("role:eng", PolicyFragment.parse({"tools": ["fs_read", "shell"]})),
            ("overlay", PolicyFragment.parse({"tools": ["fs_write"]}, replaces=True)),
        ]
    )
    assert policy.tools == ("fs_write",)
    assert policy.provenance["tools"] == "overlay"


def test_an_override_ignores_a_role_it_was_written_before():
    """A grant added to a role does not reach a list the account has overridden."""

    role = PolicyFragment.parse({"files_read": ["/srv/a", "/srv/added-later"]})
    override = PolicyFragment.parse({"files_read": ["/srv/a"]}, replaces=True)

    assert compose([("role:eng", role), ("overlay", override)]).files_read == ("/srv/a",)


def test_an_empty_override_is_empty():
    """An empty list is a statement, not an absence: the account gets none."""

    override = PolicyFragment.parse({"tools": []}, replaces=True)
    role = PolicyFragment.parse({"tools": ["shell"]})

    assert compose([("role:eng", role), ("overlay", override)]).tools == ()


def test_renaming_a_role_moves_its_key_and_declared_references():
    baseline = {
        "roles": {"me": {"tools": ["fs_read"]}, "other": {"tools": ["shell"]}},
        "users": [
            {"name": "alice", "roles": ["me"]},
            {"name": "bob", "roles": ["other", "me"]},
        ],
    }

    renamed = rename_in_baseline(baseline, "me", "full-access")

    assert renamed["roles"]["full-access"] == {"tools": ["fs_read"]}
    assert "me" not in renamed["roles"]
    assert renamed["users"][0]["roles"] == ["full-access"]
    assert renamed["users"][1]["roles"] == ["other", "full-access"]
    # The caller is about to compare-and-set the document; a rewrite that
    # mutated the live dict would make a failed write look successful.
    assert "me" in baseline["roles"]


def test_renaming_a_role_refuses_a_name_it_cannot_represent():
    baseline = {"roles": {"me": {}}, "users": []}

    with pytest.raises(RoleError, match="invalid_name"):
        rename_in_baseline(baseline, "me", "Full Access")
    with pytest.raises(RoleError, match="same_name"):
        rename_in_baseline(baseline, "me", "me")
    with pytest.raises(RoleError, match="unknown_role"):
        rename_in_baseline(baseline, "gone", "other")


def test_renaming_a_role_refuses_an_existing_target():
    baseline = {"roles": {"me": {}, "other": {}}, "users": []}

    with pytest.raises(RoleError, match="role_exists"):
        rename_in_baseline(baseline, "me", "other")


def test_unknown_field_is_rejected():
    with pytest.raises(FragmentError, match="unknown policy fields"):
        PolicyFragment.parse({"tolos": ["fs_read"]})


def test_null_scalar_means_inherit_not_reset():
    """An explicit null must not override the layer below."""

    fragment = PolicyFragment.parse({"max_iterations": None})
    assert "max_iterations" not in fragment.scalars


def test_a_network_host_list_is_rejected():
    """There is no host list, and the shape that used to be one is refused.

    A list nothing enforced read as a control — `mode = "proxy"` named a
    component that no longer exists — so policy says whether the network is
    granted and nothing more (N146).
    """

    with pytest.raises(FragmentError, match="network must be one of"):
        PolicyFragment.parse({"network": {"mode": "any", "hosts": ["example.com"]}})


# -- composition ------------------------------------------------------------


def test_lists_union_and_scalars_override():
    policy = compose(
        [
            (
                "defaults",
                PolicyFragment.parse({"tools": ["fs_read"], "max_iterations": 20}),
            ),
            (
                "role:eng",
                PolicyFragment.parse({"tools": ["fs_write"], "max_iterations": 40}),
            ),
        ]
    )
    assert policy.tools == ("fs_read", "fs_write")
    assert policy.max_iterations == 40


def test_provenance_names_the_layer_that_set_each_field():
    policy = compose(
        [
            (
                "defaults",
                PolicyFragment.parse({"tools": ["fs_read"], "max_iterations": 20}),
            ),
            ("role:eng", PolicyFragment.parse({"max_iterations": 40})),
        ]
    )
    assert policy.provenance["tools"] == "defaults"
    assert policy.provenance["max_iterations"] == "role:eng"


def test_overlay_can_revoke_what_the_baseline_granted():
    policy = compose(
        [
            ("defaults", PolicyFragment.parse({"tools": ["fs_read", "shell"]})),
            ("overlay", PolicyFragment.parse({"tools": ["fs_read"]}, replaces=True)),
        ]
    )
    assert policy.tools == ("fs_read",)


def test_defaults_are_restrictive():
    """A fragment that forgets a field must not widen anything."""

    policy = compose([])
    assert policy.auto_accept == "safe"
    assert policy.network == "none"
    assert policy.reach == frozenset()


def test_reach_is_derived_from_actual_paths_not_claims():
    """A user with no writable path has no write reach."""

    policy = compose([("defaults", PolicyFragment.parse({"files_read": ["/srv"]}))])
    assert policy.reach == frozenset({"read"})


def test_network_reach_is_the_grant_and_nothing_else():
    granted = compose([("d", PolicyFragment.parse({"network": "any"}))])
    assert "network" in granted.reach

    withheld = compose([("d", PolicyFragment.parse({"network": "none"}))])
    assert "network" not in withheld.reach

    # And the default, for a fragment that says nothing at all.
    assert "network" not in compose([]).reach


def test_policy_hash_ignores_provenance():
    """Relabelling which layer granted something is not a policy change."""

    first = compose([("defaults", PolicyFragment.parse({"tools": ["fs_read"]}))])
    second = compose([("role:eng", PolicyFragment.parse({"tools": ["fs_read"]}))])
    assert first.provenance != second.provenance
    assert policy_hash(first) == policy_hash(second)


# -- layering ---------------------------------------------------------------


@pytest.fixture
def source(tmp_path):
    return PolicySource(
        baseline={
            "schema_version": 2,
            "defaults": {"tools": ["fs_read"]},
            "roles": {"eng": {"tools": ["fs_write"]}, "ops": {"tools": ["shell"]}},
            "users": [{"name": "alice", "roles": ["eng"]}],
        },
        overlay_dir=str(tmp_path),
    )


async def test_group_grants_add_roles(source):
    directory = FakeDirectory(groups={"alice": {"ops"}})
    grants = FakeGrants({("ops", "ops")})

    layers = await layers_for(source, "alice", directory=directory, grants=grants)
    labels = [label for label, _ in layers]

    assert labels == ["defaults", "role:eng", "role:ops"]


async def test_grant_for_an_unknown_role_is_inert(source):
    """A stale grant must not break every login."""

    directory = FakeDirectory(groups={"alice": {"ops"}})
    grants = FakeGrants({("ops", "does-not-exist")})

    layers = await layers_for(source, "alice", directory=directory, grants=grants)
    assert [label for label, _ in layers] == ["defaults", "role:eng"]


async def test_roles_in_force_names_the_path_that_granted_each_role(source):
    directory = FakeDirectory(groups={"alice": {"ops"}})
    grants = FakeGrants({("ops", "ops")})

    roles = await roles_in_force(source, "alice", directory=directory, grants=grants)

    assert [(role.name, role.source, role.known) for role in roles] == [
        ("eng", "declared", True),
        ("ops", "group:ops", True),
    ]


async def test_roles_in_force_reports_a_declared_role_the_baseline_lost(source):
    """A declaration that no longer resolves is shown, not silently dropped."""

    source.baseline["users"][0]["roles"] = ["eng", "retired"]

    roles = await roles_in_force(source, "alice")

    assert [(role.name, role.known) for role in roles] == [("eng", True), ("retired", False)]


async def test_layers_and_roles_composes_the_roles_it_reports(source):
    """The account page and the policy engine must not disagree about roles."""

    directory = FakeDirectory(groups={"alice": {"ops"}})
    grants = FakeGrants({("ops", "ops")})

    layers, roles = await layers_and_roles(source, "alice", directory=directory, grants=grants)

    assert [label for label, _ in layers] == ["defaults", "role:eng", "role:ops"]
    assert {role.name for role in roles} == {"eng", "ops"}


async def test_layer_order_is_deterministic(source):
    """Two appliances with the same configuration compose the same policy."""

    directory = FakeDirectory(groups={"alice": {"ops", "eng"}})
    grants = FakeGrants({("ops", "ops"), ("eng", "eng")})

    first = await layers_for(source, "alice", directory=directory, grants=grants)
    second = await layers_for(source, "alice", directory=directory, grants=grants)
    assert [label for label, _ in first] == [label for label, _ in second]


async def test_overlay_is_applied_last(source, tmp_path):
    (tmp_path / "alice.json").write_text(json.dumps({"tools": ["fs_write"]}))
    layers = await layers_for(source, "alice")
    assert layers[-1][0] == "overlay"
    assert compose(layers).tools == ("fs_write",)


# -- the capability gate ----------------------------------------------------


def test_gate_denies_an_ungranted_tool():
    policy = EffectivePolicy(tools=("fs_read",), files_read=("/srv",))
    registry = FakeRegistry([manifest("shell", needs={"read"})])

    decision = gate.check("shell", policy, registry)
    assert not decision.allowed
    assert decision.reason == gate.NOT_GRANTED


def test_gate_denies_a_granted_tool_without_a_manifest():
    """A name the registry cannot describe is a name whose needs we cannot check."""

    policy = EffectivePolicy(tools=("ghost",), files_read=("/srv",))
    decision = gate.check("ghost", policy, FakeRegistry([]))
    assert not decision.allowed
    assert decision.reason == gate.UNKNOWN_TOOL


def test_gate_denies_when_needs_exceed_reach():
    policy = EffectivePolicy(tools=("fs_write",), files_read=("/srv",))
    registry = FakeRegistry([manifest("fs_write", needs={"write"})])

    decision = gate.check("fs_write", policy, registry)
    assert not decision.allowed
    assert decision.reason == gate.INSUFFICIENT_REACH


def test_gate_allows_when_needs_are_within_reach():
    policy = EffectivePolicy(
        tools=("fs_write",), files_read=("/srv",), files_write=("/srv/work",)
    )
    registry = FakeRegistry([manifest("fs_write", needs={"write"})])
    assert gate.check("fs_write", policy, registry).allowed


def test_grantable_matches_the_gate_exactly():
    """Advertising a tool the gate would deny teaches the model to waste turns."""

    policy = EffectivePolicy(tools=("fs_read", "fs_write"), files_read=("/srv",))
    registry = FakeRegistry(
        [manifest("fs_read", needs={"read"}), manifest("fs_write", needs={"write"})]
    )
    assert gate.grantable(policy, registry) == ["fs_read"]


# -- the working directory is a hint, not a grant ----------------------------


def test_a_working_directory_must_be_inside_policy():
    """What keeps the feature from laundering reach through the UI.

    A session can point the agent at a subdirectory of what policy grants, and
    nowhere else. Without this, typing a path into a settings field would be a way
    to reach past what policy grants.
    """

    from neuman_server.decision.policy.paths import within_reach

    policy = EffectivePolicy(files_read=("/srv/eng", "/home/alice/projects"))
    reach = {"home": "/home/alice"}

    assert within_reach("/srv/eng", policy, **reach)
    assert within_reach("/srv/eng/deep/inside", policy, **reach)
    assert within_reach("/home/alice/projects/neuman", policy, **reach)

    assert not within_reach("/etc", policy, **reach)
    assert not within_reach("/home/alice", policy, **reach)
    assert not within_reach("/srv", policy, **reach)


def test_a_sibling_with_a_shared_prefix_is_not_inside():
    """`/srv/engine` is not within `/srv/eng`.

    A `startswith` on the raw strings says it is, which would grant a directory
    policy never mentioned — and the two names differing by one character is
    exactly how that goes unnoticed.
    """

    from neuman_server.decision.policy.paths import within_reach

    policy = EffectivePolicy(files_read=("/srv/eng",))
    assert not within_reach("/srv/engine", policy, home="/home/alice")
    assert not within_reach("/srv/eng-backup", policy, home="/home/alice")


def test_a_traversal_is_refused_before_it_is_compared():
    """`..` is rejected outright rather than resolved.

    Resolving would mean the answer depends on the filesystem at check time,
    which is a different thing from the filesystem at use time — the race the ops
    channel already refuses symlinks over.
    """

    from neuman_server.decision.policy.paths import within_reach

    policy = EffectivePolicy(files_read=("/srv/eng",))
    reach = {"home": "/home/alice"}

    assert not within_reach("/srv/eng/../../etc", policy, **reach)
    assert not within_reach("../etc", policy, **reach)
    assert not within_reach("relative/path", policy, **reach)


def test_a_home_grant_names_the_whole_home_dotfiles_included():
    """`$HOME` means the home, not a subset of it (D105).

    That includes the dotfiles: `~/.ssh` and `~/.gnupg` are inside a `$HOME`
    grant, asserted here so the cost of the plain meaning is visible in a test
    rather than discovered in use. A role that wants two folders names two
    folders; no appliance-wide list narrows the token.
    """

    from neuman_server.decision.policy.paths import within_reach

    token = EffectivePolicy(files_read=("$HOME",))
    reach = {"home": "/home/alice"}

    assert within_reach("/home/alice/Documents/notes", token, **reach)
    assert within_reach("/home/alice", token, **reach)
    assert within_reach("/home/alice/.ssh/id_ed25519", token, **reach)


def test_the_token_reaches_nothing_until_a_client_reports_a_home():
    """An account is not a host account (D67), so there is no home to expand."""

    from neuman_server.decision.policy.paths import within_reach

    token = EffectivePolicy(files_read=("$HOME",))

    assert not within_reach("/home/alice", token, home=None)


def test_an_absolute_home_grant_names_that_directory_and_not_the_acting_home():
    """A path is a path; `$HOME` is the spelling that names no machine.

    The absolute entry stays a literal directory wherever the client is. Which
    is why an operator who means "their home" writes the token — an absolute
    path is right for a shared directory, and wrong for a home (N145).
    """

    from neuman_server.decision.policy.paths import within_reach

    policy = EffectivePolicy(files_read=("/home/alice",))
    reach = {"home": "/home/remote"}

    assert within_reach("/home/alice", policy, **reach)
    assert within_reach("/home/alice/.ssh", policy, **reach)
    assert not within_reach("/home/remote/Documents", policy, **reach)


# -- the approval ceiling (N88) -----------------------------------------------
#
# `auto_accept` is a user's choice; `auto_accept_max` is the operator's bound on
# it. Scalars are last-wins and the overlay is the last layer, so without the
# clamp a user fragment naming "yolo" simply beats the role that capped them —
# which is the whole feature failing silently rather than visibly.


def test_a_choice_is_clamped_to_the_ceiling():
    """The enforcement point. Composition cannot do this itself.

    `compose` applies layers in order and the last writer wins per scalar; it
    has no way to know that one field bounds another. So the relationship is
    expressed where the value is read.
    """

    from neuman_server.decision.policy.compose import EffectivePolicy

    capped = EffectivePolicy(auto_accept="yolo", auto_accept_max="safe")
    assert capped.effective_auto_accept == "safe"


def test_a_choice_within_the_ceiling_is_honoured():
    from neuman_server.decision.policy.compose import EffectivePolicy

    allowed = EffectivePolicy(auto_accept="yolo", auto_accept_max="yolo")
    assert allowed.effective_auto_accept == "yolo"


def test_a_stricter_choice_is_never_widened_to_the_ceiling():
    """A ceiling is an upper bound, not a floor.

    Reading it as "the mode to use" would silently loosen every account whose
    operator had granted headroom they had not taken — the opposite of what a
    limit is for.
    """

    from neuman_server.decision.policy.compose import EffectivePolicy

    strict = EffectivePolicy(auto_accept="never", auto_accept_max="yolo")
    assert strict.effective_auto_accept == "never"


def test_an_unknown_mode_clamps_closed():
    """A mode that escaped validation must fail closed, not open.

    `auto_accept_rank` returns 0 for anything unrecognised, so a corrupted
    overlay asks for approval rather than skipping it.
    """

    from neuman_server.decision.policy.compose import EffectivePolicy

    assert EffectivePolicy(auto_accept="banana").effective_auto_accept == "never"
    assert (
        EffectivePolicy(auto_accept="yolo", auto_accept_max="banana").effective_auto_accept
        == "never"
    )


def test_the_ceiling_defaults_permissive_so_an_operator_setting_still_applies():
    """Deliberately against the usual "default restrictive" rule, and why.

    The mode in force defaults to "safe", which is the restrictive part. The
    ceiling only bounds a *user's* movement, so defaulting it to "safe" would
    downgrade an operator who wrote `auto_accept = "yolo"` in git and never
    heard of the ceiling — making a new field a behaviour change for existing
    configurations.
    """

    from neuman_server.decision.policy.compose import EffectivePolicy

    operator_said_yolo = EffectivePolicy(auto_accept="yolo")
    assert operator_said_yolo.effective_auto_accept == "yolo"


def test_the_ceiling_is_a_total_order_over_every_mode():
    """Every pair of (choice, ceiling) resolves to the stricter of the two.

    A property rather than cases: the clamp is a `min` over ranks, and the way
    that goes wrong is one pair, not the general shape.
    """

    from neuman_server.decision.policy.compose import EffectivePolicy
    from neuman_server.decision.policy.fragments import AUTO_ACCEPT_MODES, auto_accept_rank

    for chosen in AUTO_ACCEPT_MODES:
        for ceiling in AUTO_ACCEPT_MODES:
            policy = EffectivePolicy(auto_accept=chosen, auto_accept_max=ceiling)
            result = policy.effective_auto_accept
            assert auto_accept_rank(result) == min(
                auto_accept_rank(chosen), auto_accept_rank(ceiling)
            ), f"{chosen} under {ceiling} gave {result}"


def test_the_ceiling_is_a_declared_policy_field():
    """So a baseline may state it, and so the fixture contract knows about it.

    `fragments.py` documents that approval mode is policy an operator states in
    git; the ceiling is the half that stays that way, so it has to be a field a
    baseline can carry rather than overlay-only.
    """

    from neuman_server.decision.policy.fragments import SCALAR_DEFAULTS, SCALAR_FIELDS

    assert "auto_accept_max" in SCALAR_FIELDS
    assert SCALAR_DEFAULTS["auto_accept_max"] == "yolo"


def test_every_scalar_the_parser_accepts_is_in_the_baseline_schema():
    """The two halves of a policy field, checked against each other.

    `fragments.py` states the rule: approval mode and budgets are declared
    fields of the baseline schema *as well as* the parser, because they are
    policy an operator states in git. It also records that they were once
    absent from the schema while the parser accepted them, which made a
    baseline setting any of them fail validation at startup.

    `auto_accept_max` reproduced that exactly — added to the parser, forgotten
    in the schema, and the appliance refused to load its own baseline with
    "Additional properties are not allowed". Caught by a VM boot rather than by
    anything here, so now it is caught here (N88).
    """

    import json
    from pathlib import Path

    from neuman_server.decision.policy.fragments import LIST_FIELDS, SCALAR_FIELDS

    schemas = Path(__file__).parent.parent.parent / "contracts" / "schemas"
    document = json.loads((schemas / "baseline.schema.json").read_text())

    # One `$defs.fragment` describes every layer — defaults, roles and users —
    # so checking it covers all three.
    declared = set(document["$defs"]["fragment"]["properties"])

    missing = (set(SCALAR_FIELDS) | set(LIST_FIELDS)) - declared
    assert not missing, (
        f"the parser accepts fields the baseline schema rejects: {sorted(missing)}"
    )
