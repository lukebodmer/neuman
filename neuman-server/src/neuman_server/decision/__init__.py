"""The product's judgment, by domain.

Pure decisions, no transport. Whatever this layer needs from the outside world
it declares as a port; adapters implement them and composition injects them
(docs/decisions.md D7). Domains compose through each other's public operations, never each
other's internals.
"""
