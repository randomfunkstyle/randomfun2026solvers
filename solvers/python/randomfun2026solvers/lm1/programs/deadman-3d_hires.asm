; deadman-3d_hires — GENERATED from randomfun2026solvers/deadman3d_hires.py,
; do not hand-edit. Regenerate with:
;   from randomfun2026solvers.deadman3d_hires import hires_source
;   from randomfun2026solvers.lm1.programs import PROGRAM_DIR
;   (PROGRAM_DIR / "deadman-3d_hires.asm").write_text(hires_source())
;
; A 128x96 framebuffer on hardware whose panel stops at 64x64: four 64x48
; LM-75s in a 2x2, driven through ONE command lane by the 1-of-4 router in
; lm1/d3_router.py (.unit doom4). tile = (x>=64) + 2*(y>=48); a command word
; is the DOOM unit's own 8*arg + code with the tile selector shifted in
; underneath it, so the CPU forwards words it never has to decode.
;
; The four panels commit on four separate SWAP pipes, so the frame is kept
; whole by the router's broadcast leaf (`S` — send to EVERY outgoing pipe at
; once): every panel sees the same COMMIT sequence, so tile frame N is always
; a piece of logical frame N and a composed frame is never half-old.
;
; An ungraded demo: it borrows plotter's problem JSON for registration only.

.unit doom4

.equ N      1            ; laps of 8 left this round
.equ CMIT   62           ; COMMIT on the broadcast leaf (SEL ALL)

round:  IN                  ; this round's burst, in laps of 8
        ST  N
fwd:    IN                  ; a pre-encoded router word
        SND                 ; ... straight through to its tile
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        DECM N              ; ACC = the lap count BEFORE the decrement
        SUBI 1
        BRZ done
        JMP fwd
done:   LDI CMIT
        SND                 ; SWAP 0 on all four panels at once
        JMP round
