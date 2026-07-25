"""Tools for running and scoring checked-in Little Man programs."""

__all__ = [
    "Littleman",
    "LittlemanError",
    "FanOut",
    "ProgramScore",
    "ScoringError",
    "Snapshot",
    "score_program",
]


def __getattr__(name: str) -> object:
    """Load public APIs lazily so ``python -m`` can execute submodules cleanly."""
    if name in {"Littleman", "LittlemanError", "Snapshot"}:
        from . import runner

        return getattr(runner, name)
    if name in {"ProgramScore", "ScoringError", "score_program"}:
        from . import scoring

        return getattr(scoring, name)
    if name == "FanOut":
        from . import composer

        return composer.FanOut
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
