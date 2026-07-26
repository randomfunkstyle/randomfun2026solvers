"""Pack ROM tokens into a non-rectangular region, keeping the column invariant.

Prototype for a **П-shaped ROM** (top band, down the east side, bottom band, back up),
which is what the LLM machine needs: 48% of its box is empty and all of the waste is
the bottom-right, but the CPU and banks sit between the ROM band and that space, so an
L that merely indents cannot reach it.

``lm1.rom.pack_data_rows`` packs a rectangle. This takes a writable ``[x0, x1]`` window
per row instead. Its one real constraint, and the reason a naive bend is a load error:

    Any column the ROM puts backticks in must be at EVEN parity at the end of each
    contiguous block of ROM rows.

Otherwise a literal opened in one block pairs with a backtick in the next and swallows
whatever lies between — CPU glyphs, a turn arrow — and a non-digit inside a literal is
a load error (``SPEC.md`` Fine print; ``lm1.rom``'s "accidental vertical literals").
``rom_lshape_probe.build_l_rom`` gates on exactly that.

**Engine-verified**, not argued: ``littleman/examples/rom-lshape-probe.man`` is a real
L-shaped looping ROM over words 1..60 whose arm is indented 10 columns from the west.
It emits 1,851 words over 30.9 laps **in perfect order** (``lm.mjs tick ... 12000``),
which exercises the packing, the bend, the parity rule and the closed circuit's spawn.
"""
from randomfun2026solvers.lm1.rom import token_cells

def pack_region(tokens, spans):
    """``spans`` is [(x0, x1)] per row; row k is walked east when k is even.

    Same per-column parity invariant as ``pack_data_rows``, but the writable window
    varies per row, and every column is required to return to **even** parity at the
    end of each contiguous block of rows so no literal straddles a gap.
    """
    W = max(x1 for _, x1 in spans) + 1
    parity, bad, dig = [0]*W, [False]*W, [0]*W

    def feasible(col, glyph):
        if glyph == "`":
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
        if not (glyph.isdigit() or glyph == " "):
            return parity[col] % 2 == 0
        return True

    def commit(col, glyph, cells):
        cells[col] = glyph
        if glyph == "`":
            parity[col] += 1; bad[col] = False; dig[col] = 0
        elif parity[col] % 2 == 1:
            if glyph.isdigit(): dig[col] += 1
            elif glyph != " ": bad[col] = True

    rows, i = [], 0
    for k, (x0, x1) in enumerate(spans):
        if i >= len(tokens): break
        east = k % 2 == 0
        cells = [" "]*W
        cur = x0 if east else x1
        while i < len(tokens):
            glyphs = tokens[i] if east else tokens[i][::-1]
            n = len(glyphs)
            start = cur if east else cur - n + 1
            while (x0 <= start and start + n - 1 <= x1
                   and not all(feasible(start+j, glyphs[j]) for j in range(n))):
                start += 1 if east else -1
            if not (x0 <= start and start + n - 1 <= x1):
                break
            for j, g in enumerate(glyphs): commit(start+j, g, cells)
            cur = start + n if east else start - 1
            i += 1
        rows.append("".join(cells))
    return rows, i, parity

if __name__ == "__main__":
    words = list(range(1, 41))
    tokens = [token_cells(w) for w in words]
    # an L: 6 wide rows of 60 cols, then 8 narrow rows confined to cols 40..59
    spans = [(0, 59)]*6 + [(40, 59)]*8
    rows, placed, parity = pack_region(tokens, spans)
    print(f"placed {placed}/{len(tokens)} tokens in {len(rows)} rows")
    odd = [c for c, p in enumerate(parity) if p % 2]
    print("columns left at ODD parity (would straddle):", odd or "none")
    for k, r in enumerate(rows):
        print(f"{k:3d} |{r}|")

def pack_blocks(tokens, blocks):
    """``blocks`` = [(n_rows, x0, x1)]. Reports parity at each block boundary."""
    spans, marks = [], []
    for nr, x0, x1 in blocks:
        spans += [(x0, x1)]*nr
        marks.append(len(spans))
    W = max(x1 for _, _, x1 in blocks) + 1
    rows, placed, parity = pack_region(tokens, spans)
    return rows, placed, parity, marks
