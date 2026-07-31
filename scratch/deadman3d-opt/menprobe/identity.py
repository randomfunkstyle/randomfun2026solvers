"""Byte-identity gate for the committed, non-IWAD grids.

``check_bindings``' tie clause is global, so relaxing it could in principle move
``deadman-3d``'s searched ``mem_pad`` and repaint a grid that is pinned. Both
canonical grids are rebuilt and hashed here.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/menprobe")
from common import WT  # noqa: E402

EXPECT = {
    "deadman-3d.man": "f62d63fd8e3e94fc585184e47c5887aa8888d4ea96dae1ebacfe5bff32e42904",
    "deadman-3d_taped.man": "1bc5e7919df58d86bcc0c79b698c3dd39dae02421b964cd5bcf2432f441981a0",
}


def main():
    from randomfun2026solvers.lm1 import machine as M

    ok = True
    for name, store in (("deadman-3d.man", None), ("deadman-3d_taped.man", "taped")):
        m = M.build_for("deadman-3d", **({"store": store} if store else {}))
        text = "\n".join(m.rows) + "\n"
        got = hashlib.sha256(text.encode()).hexdigest()
        on_disk = (WT / "littleman" / "examples" / name).read_bytes()
        disk = hashlib.sha256(on_disk).hexdigest()
        good = got == EXPECT[name] == disk
        ok &= good
        print(f"  {name:24s} built={got[:16]} disk={disk[:16]} "
              f"{'OK' if good else 'MISMATCH'}  mem_pad={m.mem_pad} {m.width}x{m.height}")
    print("byte-identity:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
