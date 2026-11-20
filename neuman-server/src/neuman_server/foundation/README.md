# foundation

Config (environment → settings), canonical logging (the one log-record
format, shared schema in `contracts/`), the database (Postgres: migrations,
schema, and the connection pool — the server connects as the `neuman`
role, the only role with credentials), and state files (the runtime
overlay). Exposes repository interfaces for the decision layer; decision
code never writes SQL. No business logic. Depends on nothing inside
`neuman_server`.