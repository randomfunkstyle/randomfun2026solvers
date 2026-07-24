"""Little-Man compiler + emulator (POC).

Layers:
- emu      : deterministic interpreter of the 2D grid language (task_docs/language.md)
- frontend : Python-subset AST -> HIR              (later milestone)
- lir      : HIR -> low-level IR                    (later milestone)
- layout   : LIR -> 2D grid text                    (later milestone)
"""
