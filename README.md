# randomfun2026solvers

External solver entrypoint repo for `randomfun2026claude`.

The worker invokes:

```sh
./solve --solver <name> --input <input.json> --output <output.json>
```

The input JSON contains the task payload as `task_payload_b64`. The output JSON
must contain `solution_b64`.
