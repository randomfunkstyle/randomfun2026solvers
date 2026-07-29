"""Layout manager, Phase 2: pointed at ``deadman-3d_hires``.

Phase 1 (`scratch/layout1/`) established that a declared-blocks solver can
rediscover the store's request legs. Phase 2's brief is narrower and harder:

* read the **real** configuration rather than a transcription (`capture`);
* make §7.1 binding a **solved constraint system** rather than a discovered
  failure (`bindsolve`) — given a placement, which glyphs bind wrongly and what
  interval of movement repairs them;
* validate against a known answer, ``ROM_TOUCH_DROP`` (`romtouch`);
* then room H's placement, which is a genuine open problem (`roomh`).
"""
