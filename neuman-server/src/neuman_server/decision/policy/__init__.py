"""What a user may do, composed from layered fragments."""

from .compose import EffectivePolicy, compose, policy_hash
from .fragments import FragmentError, PolicyFragment
from .gate import Decision, check, grantable
from .generations import record
from .paths import expand_paths, within_reach
from .source import PolicySource, effective_policy, layers_for

__all__ = [
    "Decision",
    "EffectivePolicy",
    "FragmentError",
    "PolicyFragment",
    "PolicySource",
    "check",
    "compose",
    "effective_policy",
    "expand_paths",
    "grantable",
    "layers_for",
    "policy_hash",
    "record",
    "within_reach",
]
