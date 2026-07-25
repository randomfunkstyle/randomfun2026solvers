; palette — GENERATED from palette.json, do not hand-edit.
;
; Sixteen frames, colour 0 through 15, on the 8x8 LM-75. Uses all three port
; opcodes: DSPA parks the cursor at (0,0), DSPD paints, DSPS commits.
;
; Writing 0 to SWAP commits *and* clears `next` and resets the cursor, so the
; DSPA is strictly redundant — it is here because a display CPU that cannot
; address the panel is not one, and this is the program the hardware is
; generated from. The 64 DSPD writes are unrolled: the DATA port advances the
; cursor by itself, so painting a frame needs no counter and no STORE traffic.
;
; Address 1, not 0: the generated hardware puts the operation in the *sign* of
; the address word, so slot 0 would be ambiguous.

.equ COLOUR 1

        LDI 0
        ST  COLOUR

frame:  LDI 0
        DSPA                ; cursor -> (0, 0)
        LD  COLOUR
        DSPD                ; pixel 0
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD                ; pixel 63

        LDI 0
        DSPS                ; commit the frame, clear `next`, home the cursor

        LD  COLOUR
        ADDI 1
        ST  COLOUR          ; ST preserves ACC, so the test below sees colour + 1
        SUBI 16
        BRZ done
        JMP frame
done:   HALT
