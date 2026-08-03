"""pytest -p batch4_plugin — run the whole suite as if JUMP_V4_P2_BATCH shipped at 4.

A knob that defaults off is only half-tested by a suite that never turns it on;
this is how the *on* half gets the same 2913 tests.
"""


def pytest_configure(config):
    from randomfun2026solvers import memory_tape as mt

    mt.JUMP_V4_P2_BATCH = 4
