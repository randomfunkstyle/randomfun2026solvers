# randomfun2026solvers

External solver entrypoint repo for `randomfun2026claude`.

The worker invokes:

```sh
./solve --solver <name> --input <input.json> --output <output.json>
```

The input JSON contains runner metadata plus a nested `task` object. The task
payload is available as `task.payload_b64`. The output JSON must contain
`solution_b64`.
