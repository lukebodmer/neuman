# decision

The decision logic: identity and sessions, the policy engine (fragment
composition, the capability gate), the agent harness loop, and the audit
chain. Pure decisions, no transport: this layer knows
nothing about HTTP, Unix sockets, or provider APIs. Whatever it needs from the
outside world it declares as a port (a Protocol); adapters implement the
ports and composition injects them.

Audit events are emitted by the operations here, never by a transport
surface — a surface cannot bypass the audit chain.

Operations are owned by their domain: an operation touching one domain
lives in it (`policy/ops.py` for authoring), guarded and audited inside
the operation. `ops/` holds only irreducibly cross-domain operations
(backup, export) and may call domain public APIs only — never tables,
never another domain's internals. Operator authorization is identity's
job (`identity/authz.py`), checked inside the operation, not by the
surface.

Identity is policy: grants are keyed to groups resolved through the
`IdentityDirectory` port, and the groups are rows in Neuman's own database, so
a revocation is read on the next policy composition (D16, D68).

Allowed imports: foundation. Declares ports; never imports adapters.