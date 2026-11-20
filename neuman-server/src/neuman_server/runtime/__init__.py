"""The two processes beside the server that touch model weights (D78, D79).

`neuman-runtime` holds the GPU and nothing else: it starts `llama-server` in
router mode for every local model. `neuman-fetch` holds the network and
`modelsDir` and nothing else: it downloads a preset's pinned files and verifies
them. Neither has what the other has, and the server has neither.

Both read the presets the server ships, from the same package, and both take
from the server only names and typed values. What a runtime is started with —
the binary, the files, every flag — is built here from the presets, so the
document a compromised server could write chooses among shipped models and
bounded numbers, never an argument or a path.

Standard library only, checked by `tests/test_runtime.py`: these run in units
of their own, and a dependency present for the server and absent here is a
failure that only shows at runtime (N47).
"""
