# Bash Solver Instructions

This directory contains shell-based solver entrypoints.

Run the Bash smoke solver from the repository root through the shared worker
contract:

```sh
./solve --solver shell-smoke --input /tmp/input.json --output /tmp/output.json
```

You can also run this directory's entrypoint directly when debugging:

```sh
./solvers/bash/solve --solver shell-smoke --input /tmp/input.json --output /tmp/output.json
```

The Bash solver currently ignores the input payload and writes a smoke solution
envelope with `{"smoke": true}`.
