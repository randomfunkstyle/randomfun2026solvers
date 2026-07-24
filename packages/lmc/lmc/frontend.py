"""Python-subset frontend + lowering to a little-man instruction stream.

M1 scope: straight-line programs of the form

    x = recv()          # optional single pinned variable
    emit(<expr>)        # one or more; expr over x, recv() and int constants
    halt()              # optional

Lowering model (dictated by the ISA): you cannot load a constant/var directly
into B -- only into A, or A->B via M/W.  So we keep the read variable pinned in
B, accumulate in A, load constants into A, and let each binary op pull the var
from B.  Constant-on-the-right operations use the W-trick once the var is dead.

Anything needing two simultaneously-live computed values needs the memory ring
(next milestone) and raises Unsupported here.
"""

from __future__ import annotations

import ast

# op -> little-man instruction char (A = A op B)
BINOP = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.FloorDiv: "/",
    ast.Mod: "%",
    ast.LShift: "{",
    ast.RShift: "}",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "~",
}
COMMUTATIVE = {ast.Add, ast.Mult, ast.BitAnd, ast.BitOr, ast.BitXor}


class Unsupported(Exception):
    pass


class Program:
    """Lowered result: the man's instruction stream + which I/O rooms are needed."""

    def __init__(self):
        self.code: list[str] = []
        self.uses_input = False
        self.uses_output = False

    def emit(self, s: str) -> None:
        self.code.append(s)

    @property
    def stream(self) -> str:
        return "".join(self.code)


def loadconst(c: int) -> str:
    """Instructions that leave the constant c in A (B untouched for 0..9)."""
    if 0 <= c <= 9:
        return str(c)
    if c < 0:
        return f"`{abs(c)}`N"
    return f"`{c}`"


class Lowerer:
    def __init__(self):
        self.prog = Program()
        self.var: str | None = None  # name pinned in B
        self.var_live = False

    # ---- expression -> code leaving value in A -----------------------
    def expr(self, node: ast.expr) -> None:
        p = self.prog
        if isinstance(node, ast.Constant):
            p.emit(loadconst(int(node.value)))
            return
        if isinstance(node, ast.Name):
            # the pinned variable lives in B; bring it into A (A assumed dead)
            if node.id != self.var:
                raise Unsupported(f"unknown name {node.id!r}")
            p.emit("W")  # A<->B : A=var, B=old-A
            return
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "recv":
                p.uses_input = True
                p.emit("r")
                return
            raise Unsupported(f"call {ast.dump(node)}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            self.expr(node.operand)
            p.emit("N")
            return
        if isinstance(node, ast.BinOp):
            self._binop(node)
            return
        raise Unsupported(ast.dump(node))

    def _binop(self, node: ast.BinOp) -> None:
        p = self.prog
        op = type(node.op)
        if op not in BINOP:
            raise Unsupported(f"operator {op.__name__}")
        ch = BINOP[op]
        L, R = node.left, node.right

        def is_var(n):
            return isinstance(n, ast.Name) and n.id == self.var

        def is_const(n):
            return isinstance(n, ast.Constant)

        # var op var: duplicate the pinned var into A
        if is_var(L) and is_var(R):
            p.emit("W")  # A=var, B=old
            p.emit("M")  # B=var
            p.emit(ch)  # A = var op var
            self.var_live = False
            return
        # A = <L> op var          (var from B, stays pinned)
        if is_var(R) and not is_var(L):
            self.expr(L)
            p.emit(ch)
            return
        # A = const op var  /  var op const(commutative): load const into A, op B=var
        if is_var(R) and is_const(L):
            p.emit(loadconst(int(L.value)))
            p.emit(ch)
            return
        if is_var(L) and is_const(R):
            if op in COMMUTATIVE:
                p.emit(loadconst(int(R.value)))
                p.emit(ch)
                return
            # non-commutative var op const, with A=const,B=var we'd get const op var.
            # want var op const: A=var, B=const via W-trick (var dies).
            p.emit("W")  # A=var, B=old
            p.emit("M")  # B=var
            p.emit(loadconst(int(R.value)))  # A=const
            p.emit("W")  # A=var, B=const
            p.emit(ch)
            self.var_live = False
            return
        # var op <compound>: compute compound into A (keeps B=var), then op
        if is_var(L) and not is_const(R):
            self.expr(R)  # A=R, B=var preserved (R references var via B-ops)
            if op not in COMMUTATIVE:
                p.emit("W")  # A=var, B=R  -> var op R
            p.emit(ch)
            return
        # <expr> op recv(): recv reads into A without touching B, so park L in B.
        def is_recv(n):
            return (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "recv"
            )

        if is_recv(R):
            self.expr(L)  # A = L
            p.emit("M")  # B = L
            self.expr(R)  # A = recv (B preserved)
            if op not in COMMUTATIVE:
                p.emit("W")  # A=L, B=R -> L op R
            p.emit(ch)
            return
        # A = const op <expr>: A holds const already, B free -> compute expr into B? no.
        if is_const(L) and not is_var(R):
            # load const, then op needs B=<R>; only works if R is var (handled) -- else
            # we would need a second live value: unsupported without memory.
            raise Unsupported("const op compound needs memory")
        # A = <expr> op const : compute expr into A, then W-trick (var dead)
        if is_const(R) and not is_var(L):
            self.expr(L)
            p.emit("W")
            p.emit(loadconst(int(R.value)))
            p.emit("W")
            p.emit(ch)
            self.var_live = False
            return
        # both sides compound / two live values -> needs the memory ring
        raise Unsupported("binary op with two live subexpressions needs memory")

    # ---- statements --------------------------------------------------
    def stmt(self, node: ast.stmt) -> None:
        p = self.prog
        if isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "recv"
            ):
                if self.var is not None:
                    raise Unsupported("only one pinned variable in M1")
                self.var = node.targets[0].id
                self.var_live = True
                p.uses_input = True
                p.emit("r")  # A = value
                p.emit("W")  # pin in B, clear A
                return
            raise Unsupported(f"assignment {ast.dump(node)}")
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            fn = call.func.id if isinstance(call.func, ast.Name) else None
            if fn == "emit":
                self.expr(call.args[0])
                p.uses_output = True
                p.emit("s")
                return
            if fn == "halt":
                p.emit("H")
                return
            raise Unsupported(f"call {fn}")
        raise Unsupported(ast.dump(node))

    def lower(self, src: str) -> Program:
        tree = ast.parse(src)
        for node in tree.body:
            self.stmt(node)
        # ensure the man stops
        if not self.prog.code or self.prog.code[-1] != "H":
            self.prog.emit("H")
        return self.prog


def lower(src: str) -> Program:
    return Lowerer().lower(src)
