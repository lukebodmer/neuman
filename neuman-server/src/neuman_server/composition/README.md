# composition

App assembly and the transport surfaces: the session surface (HTTP/WS, for
the client, its chat window and anything else holding a session), the
`/client` channel a daemon dispatches over, and the admin API (Unix socket,
peer credentials). This layer wires
foundation, decision, and adapters together and owns process-level
concerns: lifespan, startup ordering, health, request correlation, and
background jobs (`jobs/` — retention purge, index rebuilds, settle sweeps,
watchers). It makes no decisions of its own: a request arrives, identity is
resolved, a core operation is called, the response is serialized.

Allowed imports: everything. Nothing imports composition.