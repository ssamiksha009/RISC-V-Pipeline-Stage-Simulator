from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

REG_NAMES = {f"x{i}": i for i in range(32)}

def parse_reg(tok: str) -> int:
    tok = tok.strip().lower()
    if tok not in REG_NAMES:
        raise ValueError(f"Unknown register '{tok}'")
    return REG_NAMES[tok]

def parse_int(s: str) -> int:
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s)

@dataclass
class Instruction:
    op: str
    rd: Optional[int] = None
    rs1: Optional[int] = None
    rs2: Optional[int] = None
    imm: Optional[int] = None
    branch_target_idx: Optional[int] = None  # absolute index into program
    raw: str = ""
    iid: int = -1  # unique instruction id for visualization

    @property
    def is_nop(self) -> bool:
        return self.op == "nop"

    def writes_rd(self) -> bool:
        return self.op in ("add", "sub", "lw")

    def uses_reg(self, reg: int) -> bool:
        if self.is_nop or reg == 0:
            return False
        opset = []
        if self.op in ("add", "sub", "beq"):
            opset = [self.rs1, self.rs2]
        elif self.op in ("lw", "sw"):
            # lw uses base (rs1); sw uses base (rs1) and source (rs2)
            opset = [self.rs1] + ([self.rs2] if self.op == "sw" else [])
        return reg in opset

def _clean_line(line: str) -> str:
    # Strip comments starting with '#' or ';'
    for c in ("#", ";"):
        if c in line:
            line = line.split(c, 1)[0]
    return line.strip()

def parse_program(asm_text: str) -> List[Instruction]:
    """
    Two-pass parser with labels.
    Supports:
      add rd, rs1, rs2
      sub rd, rs1, rs2
      lw  rd, offset(rs1)
      sw  rs2, offset(rs1)
      beq rs1, rs2, label
      nop
    """
    lines = asm_text.splitlines()
    cleaned: List[str] = []
    for ln in lines:
        cl = _clean_line(ln)
        if cl:
            cleaned.append(cl)

    # Pass 1: collect labels and raw op lines
    labels: Dict[str, int] = {}
    ops: List[Tuple[str, Optional[str]]] = []  # (line, label_name_if_any)
    pc = 0
    for line in cleaned:
        if line.endswith(":"):
            lbl = line[:-1].strip()
            if not lbl:
                raise ValueError("Empty label name")
            labels[lbl] = pc
        else:
            ops.append((line, None))
            pc += 1

    # Pass 2: build Instruction objects
    program: List[Instruction] = []
    iid_counter = 0

    for line, _ in ops:
        raw = line
        toks = line.replace(",", " ").split()
        op = toks[0].lower()

        def new_i(**kw):
            nonlocal iid_counter
            ins = Instruction(iid=iid_counter, raw=raw, **kw)
            iid_counter += 1
            return ins

        if op == "nop":
            program.append(new_i(op="nop"))
        elif op in ("add", "sub"):
            # add rd, rs1, rs2
            if len(toks) != 4:
                raise ValueError(f"Bad {op} format: {raw}")
            rd = parse_reg(toks[1])
            rs1 = parse_reg(toks[2])
            rs2 = parse_reg(toks[3])
            program.append(new_i(op=op, rd=rd, rs1=rs1, rs2=rs2))
        elif op == "lw":
            # lw rd, offset(rs1)
            if len(toks) != 3:
                raise ValueError(f"Bad lw format: {raw}")
            rd = parse_reg(toks[1])
            # parse offset(rs1)
            addr = toks[2]
            if "(" not in addr or not addr.endswith(")"):
                raise ValueError(f"Bad lw address: {raw}")
            off_str, reg_str = addr.split("(", 1)
            rs1 = parse_reg(reg_str[:-1])
            imm = parse_int(off_str or "0")
            program.append(new_i(op="lw", rd=rd, rs1=rs1, imm=imm))
        elif op == "sw":
            # sw rs2, offset(rs1)
            if len(toks) != 3:
                raise ValueError(f"Bad sw format: {raw}")
            rs2 = parse_reg(toks[1])
            addr = toks[2]
            if "(" not in addr or not addr.endswith(")"):
                raise ValueError(f"Bad sw address: {raw}")
            off_str, reg_str = addr.split("(", 1)
            rs1 = parse_reg(reg_str[:-1])
            imm = parse_int(off_str or "0")
            program.append(new_i(op="sw", rs1=rs1, rs2=rs2, imm=imm))
        elif op == "beq":
            # beq rs1, rs2, label|imm_index
            if len(toks) != 4:
                raise ValueError(f"Bad beq format: {raw}")
            rs1 = parse_reg(toks[1])
            rs2 = parse_reg(toks[2])
            label = toks[3]
            target_idx: Optional[int] = None
            if label in labels:
                target_idx = labels[label]
            else:
                # allow numeric absolute instruction index as fallback
                try:
                    target_idx = parse_int(label)
                except Exception:
                    raise ValueError(f"Unknown label '{label}' in: {raw}")
            program.append(new_i(op="beq", rs1=rs1, rs2=rs2, branch_target_idx=target_idx))
        else:
            raise ValueError(f"Unsupported op '{op}' in: {raw}")

    return program

# Small helper NOP singleton for convenience if needed outside
NOP = Instruction(op="nop", raw="nop", iid=-42)
