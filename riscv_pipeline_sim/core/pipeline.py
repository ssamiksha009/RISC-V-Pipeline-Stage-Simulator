from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional
from .instructions import Instruction, NOP

STAGES = ["IF", "ID", "EX", "MEM", "WB"]

def ins_str(ins: Instruction) -> str:
    return "NOP" if ins.is_nop else ins.raw

@dataclass
class LatchIFID:
    instr: Instruction = field(default_factory=lambda: NOP)
    pc: int = 0
    predicted_taken: bool = False  # carried for branch predictor

@dataclass
class LatchIDEX:
    instr: Instruction = field(default_factory=lambda: NOP)
    pc: int = 0
    rs1: int = 0
    rs2: int = 0
    rd: int = 0
    imm: int = 0
    val1: int = 0
    val2: int = 0
    predicted_taken: bool = False

@dataclass
class LatchEXMEM:
    instr: Instruction = field(default_factory=lambda: NOP)
    pc: int = 0
    rd: int = 0
    alu: int = 0
    store_val: int = 0
    branch_taken: bool = False
    branch_target: int = 0
    predicted_taken: bool = False

@dataclass
class LatchMEMWB:
    instr: Instruction = field(default_factory=lambda: NOP)
    pc: int = 0                       # NEW: track pc through WB for accurate Gantt/WB
    rd: int = 0
    wb_val: int = 0
    alu: int = 0
    mem_read_val: int = 0

@dataclass
class PipelineCPU:
    program: List[Instruction] = field(default_factory=list)
    pc: int = 0
    cycle: int = 0
    regs: List[int] = field(default_factory=lambda: [0]*32)
    dmem: Dict[int, int] = field(default_factory=dict)
    forwarding: bool = True
    structural_on: bool = False

    # Branch predictor: "none" | "static_nt" | "onebit"
    predictor_mode: str = "none"
    predictor_table: Dict[int, bool] = field(default_factory=dict)  # pc -> last outcome

    # Metrics & tracing
    retired: int = 0
    stalls: int = 0
    flushes: int = 0
    mispredicts: int = 0
    trace: List[Dict[str, str]] = field(default_factory=list)  # stage->instr (for compact timeline)

    # NEW: deeper teaching state
    trace_stage_pc: List[Dict[str, Optional[int]]] = field(default_factory=list)  # stage->pc @ start-of-cycle
    inst_meta: Dict[int, str] = field(default_factory=dict)     # pc -> raw text
    inst_status: Dict[int, Dict[str, Any]] = field(default_factory=dict)  # {pc:{retired,retire_cycle,last_stage}}
    stall_breakdown: Dict[str, int] = field(default_factory=dict)         # reason -> count
    addr_log: List[Dict[str, Any]] = field(default_factory=list)          # memory/alu events for teaching

    IF_ID: LatchIFID = field(default_factory=LatchIFID)
    ID_EX: LatchIDEX = field(default_factory=LatchIDEX)
    EX_MEM: LatchEXMEM = field(default_factory=LatchEXMEM)
    MEM_WB: LatchMEMWB = field(default_factory=LatchMEMWB)

    last_events: Dict[str, Any] = field(default_factory=dict)

    # ---------- Lifecycle ----------
    def reset(self):
        self.pc = 0
        self.cycle = 0
        self.regs = [0]*32
        self.dmem = {}
        self.IF_ID = LatchIFID()
        self.ID_EX = LatchIDEX()
        self.EX_MEM = LatchEXMEM()
        self.MEM_WB = LatchMEMWB()
        self.last_events = {}
        self.retired = 0
        self.stalls = 0
        self.flushes = 0
        self.mispredicts = 0
        self.trace = []
        self.predictor_table = {}
        self.trace_stage_pc = []
        self.inst_status = {}
        self.stall_breakdown = {}
        self.addr_log = []

    def load_program(self, prog: List[Instruction]):
        self.reset()
        self.program = prog
        # Build pc -> raw map & initial status
        self.inst_meta = {}
        self.inst_status = {}
        for pc, ins in enumerate(self.program):
            self.inst_meta[pc] = ins_str(ins)
            self.inst_status[pc] = {"retired": False, "retire_cycle": None, "last_stage": "-"}

    # ---------- Helpers ----------
    def _fetch(self, pc: int) -> Instruction:
        return self.program[pc] if 0 <= pc < len(self.program) else NOP

    def _alu_compute(self, instr: Instruction, a: int, b: int) -> int:
        if instr.op == "add": return a + b
        if instr.op == "sub": return a - b
        if instr.op in ("lw", "sw"): return a + (instr.imm or 0)
        return 0

    def _read_reg(self, idx: int) -> int:
        return 0 if idx == 0 else self.regs[idx]

    def _forward_get(self, src_reg: int, default_val: int) -> Tuple[int, str]:
        if not self.forwarding or src_reg == 0:
            return default_val, "none"
        exm = self.EX_MEM
        if (not exm.instr.is_nop) and exm.instr.writes_rd() and exm.rd == src_reg:
            if exm.instr.op != "lw":
                return exm.alu, "EX/MEM"
        mw = self.MEM_WB
        if (not mw.instr.is_nop) and mw.instr.writes_rd() and mw.rd == src_reg:
            return mw.wb_val, "MEM/WB"
        return default_val, "none"

    def _detect_stall_decode(self) -> Tuple[bool, str, Dict[str, Any]]:
        id_ins = self.IF_ID.instr
        if id_ins.is_nop:
            return False, "", {}
        ex_ins = self.ID_EX.instr
        detail = {
            "producer_pc": self.ID_EX.pc if not ex_ins.is_nop else None,
            "producer_op": ex_ins.op if not ex_ins.is_nop else "",
            "producer_rd": ex_ins.rd if not ex_ins.is_nop else None,
            "consumer_op": id_ins.op,
            "uses": [id_ins.rs1 or 0, id_ins.rs2 or 0],
        }
        if self.forwarding:
            if (not ex_ins.is_nop) and ex_ins.op == "lw" and ex_ins.rd and id_ins.uses_reg(ex_ins.rd):
                return True, "lw-use", detail
            return False, "", detail
        # Forwarding OFF: RAW against EX/MEM/WB
        if (not ex_ins.is_nop) and ex_ins.writes_rd() and ex_ins.rd and id_ins.uses_reg(ex_ins.rd):
            return True, "RAW vs EX", detail
        if (not self.EX_MEM.instr.is_nop) and self.EX_MEM.instr.writes_rd() and self.EX_MEM.rd and id_ins.uses_reg(self.EX_MEM.rd):
            return True, "RAW vs MEM", detail
        if (not self.MEM_WB.instr.is_nop) and self.MEM_WB.instr.writes_rd() and self.MEM_WB.rd and id_ins.uses_reg(self.MEM_WB.rd):
            return True, "RAW vs WB", detail
        return False, "", detail

    def _predict_taken(self, instr: Instruction, pc: int) -> bool:
        if instr.is_nop or instr.op != "beq":
            return False
        if self.predictor_mode == "none":
            return False
        if self.predictor_mode == "static_nt":
            return False
        if self.predictor_mode == "onebit":
            return bool(self.predictor_table.get(pc, False))
        return False

    # ---------- Public info for GUI ----------
    @property
    def cpi(self) -> float:
        return (self.cycle / self.retired) if self.retired else float("inf")

    def get_latches_snapshot(self) -> Dict[str, Any]:
        return {
            "IF/ID": {"pc": self.IF_ID.pc, "instr": ins_str(self.IF_ID.instr), "pred": self.IF_ID.predicted_taken},
            "ID/EX": {"pc": self.ID_EX.pc, "instr": ins_str(self.ID_EX.instr), "rs1": self.ID_EX.rs1,
                      "rs2": self.ID_EX.rs2, "rd": self.ID_EX.rd, "imm": self.ID_EX.imm,
                      "v1": self.ID_EX.val1, "v2": self.ID_EX.val2, "pred": self.ID_EX.predicted_taken},
            "EX/MEM": {"pc": self.EX_MEM.pc, "instr": ins_str(self.EX_MEM.instr), "rd": self.EX_MEM.rd,
                       "alu": self.EX_MEM.alu, "store": self.EX_MEM.store_val,
                       "btaken": self.EX_MEM.branch_taken, "target": self.EX_MEM.branch_target,
                       "pred": self.EX_MEM.predicted_taken},
            "MEM/WB": {"pc": self.MEM_WB.pc, "instr": ins_str(self.MEM_WB.instr), "rd": self.MEM_WB.rd,
                       "alu": self.MEM_WB.alu, "wb": self.MEM_WB.wb_val}
        }

    def decode_control(self, instr: Instruction) -> Dict[str, Any]:
        sig = {
            "RegWrite": False, "MemRead": False, "MemWrite": False,
            "MemToReg": False, "Branch": False, "ALUSrc": "reg", "ALUOp": "add"
        }
        if instr.is_nop:
            return sig
        op = instr.op
        if op in ("add", "sub"):
            sig.update(RegWrite=True, ALUSrc="reg", ALUOp=("sub" if op == "sub" else "add"))
        elif op == "lw":
            sig.update(RegWrite=True, MemRead=True, MemToReg=True, ALUSrc="imm", ALUOp="add")
        elif op == "sw":
            sig.update(MemWrite=True, ALUSrc="imm", ALUOp="add")
        elif op == "beq":
            sig.update(Branch=True, ALUSrc="reg", ALUOp="sub")
        return sig

    def get_control_snapshot(self) -> Dict[str, Any]:
        return self.decode_control(self.IF_ID.instr)

    def get_predictor_snapshot(self) -> List[Tuple[int, str, str]]:
        out = []
        for pc, state in sorted(self.predictor_table.items()):
            out.append((pc, self.inst_meta.get(pc, ins_str(self._fetch(pc))), "T" if state else "NT"))
        return out

    def get_inflight(self) -> List[Dict[str, Any]]:
        lst = []
        if not self.IF_ID.instr.is_nop:  lst.append({"stage": "IF",  "instr": ins_str(self.IF_ID.instr)})
        if not self.ID_EX.instr.is_nop:  lst.append({"stage": "ID",  "instr": ins_str(self.ID_EX.instr), "rs1": self.ID_EX.rs1, "rs2": self.ID_EX.rs2, "rd": self.ID_EX.rd})
        if not self.EX_MEM.instr.is_nop: lst.append({"stage": "EX",  "instr": ins_str(self.EX_MEM.instr), "rd": self.EX_MEM.rd})
        if not self.MEM_WB.instr.is_nop: lst.append({"stage": "MEM/WB", "instr": ins_str(self.MEM_WB.instr), "rd": self.MEM_WB.rd})
        return lst

    def get_cpi_breakdown(self) -> Dict[str, Any]:
        cycles = max(1, self.cycle)
        return {
            "cycles": cycles,
            "useful_pct": (self.retired / cycles) * 100.0,
            "stall_pct": (self.stalls / cycles) * 100.0,
            "flush_pct": (self.flushes / cycles) * 100.0,  # teaching estimate
            "mispredicts": self.mispredicts,
        }

    def get_stall_breakdown(self) -> List[Tuple[str, int]]:
        items = sorted(self.stall_breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
        return items

    def get_addr_log(self, last_n: int = 80) -> List[Dict[str, Any]]:
        return self.addr_log[-last_n:]

    def get_program_status(self) -> List[Tuple[int, str, str, bool, Optional[int]]]:
        rows = []
        for pc in range(len(self.program)):
            meta = self.inst_meta.get(pc, "")
            st = self.inst_status.get(pc, {"last_stage": "-", "retired": False, "retire_cycle": None})
            rows.append((pc, meta, st["last_stage"], st["retired"], st["retire_cycle"]))
        return rows

    def get_gantt_window(self, max_cycles: int = 60) -> Tuple[List[int], List[str], List[List[str]]]:
        """
        Returns (cycle_indices, row_labels, matrix) where matrix[row][col] is stage letter or '.'.
        Rows correspond to program PCs in order; cycle_indices are the last up to max_cycles.
        """
        # We use trace_stage_pc (start-of-cycle view)
        window = self.trace_stage_pc[-max_cycles:]
        start_cycle_index = max(1, self.cycle - len(window) + 1)
        cycles = list(range(start_cycle_index, start_cycle_index + len(window)))

        # Map stage name -> letter
        stage_letter = {"IF": "I", "ID": "D", "EX": "E", "MEM": "M", "WB": "W"}
        n_rows = len(self.program)
        mat: List[List[str]] = [[ "." for _ in cycles ] for _ in range(n_rows)]
        for c_idx, pcs in enumerate(window):
            for st, pc in pcs.items():
                if pc is None: continue
                if 0 <= pc < n_rows:
                    mat[pc][c_idx] = stage_letter.get(st, "?")
        row_labels = [self.inst_meta.get(pc, "") for pc in range(n_rows)]
        return cycles, row_labels, mat

    def export_csv(self, path: str):
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["cycle", "IF", "ID", "EX", "MEM", "WB"])
            for i, row in enumerate(self.trace, start=1):
                w.writerow([i, row.get("IF",""), row.get("ID",""), row.get("EX",""), row.get("MEM",""), row.get("WB","")])

    # ---------- One cycle ----------
    def step(self) -> Dict[str, str]:
        # Stage occupancy snapshot at START of cycle (for Gantt)
        pcs_start = {
            "IF":  self.IF_ID.pc if not self.IF_ID.instr.is_nop else None,
            "ID":  self.ID_EX.pc if not self.ID_EX.instr.is_nop else None,
            "EX":  self.EX_MEM.pc if not self.EX_MEM.instr.is_nop else None,
            "MEM": self.EX_MEM.pc if not self.EX_MEM.instr.is_nop else None,  # teaching: MEM operates on EX/MEM
            "WB":  self.MEM_WB.pc if not self.MEM_WB.instr.is_nop else None,
        }
        self.trace_stage_pc.append(pcs_start)

        self.last_events = {
            "stall": False, "stall_reason": "", "hazard_detail": {},
            "branch_taken": False, "mispredict": False, "structural_stall": False,
            "fwd_a": "none", "fwd_b": "none"
        }

        # WB: commit previous instruction
        if not self.MEM_WB.instr.is_nop:
            self.retired += 1
            wb_pc = self.MEM_WB.pc
            if wb_pc in self.inst_status:
                self.inst_status[wb_pc]["retired"] = True
                self.inst_status[wb_pc]["retire_cycle"] = self.cycle
                self.inst_status[wb_pc]["last_stage"] = "WB"
            if self.MEM_WB.instr.writes_rd():
                rd = self.MEM_WB.rd or 0
                if rd != 0:
                    self.regs[rd] = self.MEM_WB.wb_val

        # Hazards for ID
        stall_decode, reason, detail = self._detect_stall_decode()
        if stall_decode:
            self.stalls += 1
            self.stall_breakdown[reason] = self.stall_breakdown.get(reason, 0) + 1
            self.last_events.update({"stall": True, "stall_reason": reason, "hazard_detail": detail})

        # EX
        ex_instr = self.ID_EX.instr
        a_val, fsrc_a = self._forward_get(self.ID_EX.rs1, self.ID_EX.val1)
        b_val, fsrc_b = self._forward_get(self.ID_EX.rs2, self.ID_EX.val2)
        self.last_events["fwd_a"] = fsrc_a
        self.last_events["fwd_b"] = fsrc_b

        next_EX_MEM = LatchEXMEM(instr=ex_instr, pc=self.ID_EX.pc, rd=self.ID_EX.rd,
                                 predicted_taken=self.ID_EX.predicted_taken)

        if not ex_instr.is_nop:
            if ex_instr.op == "beq":
                taken = (a_val == b_val)
                next_EX_MEM.branch_taken = taken
                next_EX_MEM.branch_target = ex_instr.branch_target_idx or 0
                # log comparator via ALU-style sub result == 0 (teaching)
                self.addr_log.append({"cycle": self.cycle, "kind": "branch_cmp", "pc": self.ID_EX.pc,
                                      "a": a_val, "b": b_val, "taken": taken})
            else:
                alu = self._alu_compute(ex_instr, a_val, b_val)
                next_EX_MEM.alu = alu
                self.addr_log.append({"cycle": self.cycle, "kind": "alu", "pc": self.ID_EX.pc, "op": ex_instr.op, "a": a_val, "b": b_val, "alu": alu})
                if ex_instr.op == "sw":
                    s_val, _ = self._forward_get(self.ID_EX.rs2, self.ID_EX.val2)
                    next_EX_MEM.store_val = s_val

        # MEM
        cur_EX_MEM = self.EX_MEM
        next_MEM_WB = LatchMEMWB(instr=cur_EX_MEM.instr, pc=cur_EX_MEM.pc, rd=cur_EX_MEM.rd, alu=cur_EX_MEM.alu)

        if not cur_EX_MEM.instr.is_nop:
            if cur_EX_MEM.instr.op == "lw":
                addr = cur_EX_MEM.alu
                val = self.dmem.get(addr, 0)
                next_MEM_WB.mem_read_val = val
                next_MEM_WB.wb_val = val
                self.addr_log.append({"cycle": self.cycle, "kind": "lw", "pc": cur_EX_MEM.pc, "addr": addr, "val": val})
            elif cur_EX_MEM.instr.op == "sw":
                addr = cur_EX_MEM.alu
                self.dmem[addr] = cur_EX_MEM.store_val
                self.addr_log.append({"cycle": self.cycle, "kind": "sw", "pc": cur_EX_MEM.pc, "addr": addr, "val": cur_EX_MEM.store_val})
            elif cur_EX_MEM.instr.op in ("add", "sub"):
                next_MEM_WB.wb_val = cur_EX_MEM.alu

        # ID -> next ID/EX
        if stall_decode:
            next_ID_EX = LatchIDEX(instr=NOP)
        else:
            id_instr = self.IF_ID.instr
            if id_instr.is_nop:
                next_ID_EX = LatchIDEX(instr=NOP)
            else:
                rs1 = id_instr.rs1 or 0
                rs2 = id_instr.rs2 or 0
                rd = id_instr.rd or 0
                next_ID_EX = LatchIDEX(
                    instr=id_instr,
                    pc=self.IF_ID.pc,
                    rs1=rs1, rs2=rs2, rd=rd,
                    imm=id_instr.imm or 0,
                    val1=self._read_reg(rs1),
                    val2=self._read_reg(rs2),
                    predicted_taken=self.IF_ID.predicted_taken if id_instr.op == "beq" else False
                )

        # IF (consider structural hazard & prediction)
        fetch_stall = stall_decode
        if self.structural_on and (not cur_EX_MEM.instr.is_nop) and cur_EX_MEM.instr.op in ("lw", "sw"):
            fetch_stall = True
            self.last_events["structural_stall"] = True
            self.stalls += 1
            self.stall_breakdown["structural"] = self.stall_breakdown.get("structural", 0) + 1

        if not fetch_stall:
            fetched = self._fetch(self.pc)
            predicted_taken = self._predict_taken(fetched, self.pc)
            next_pc = self.pc + 1
            if predicted_taken and fetched.op == "beq":
                next_pc = fetched.branch_target_idx or (self.pc + 1)
            next_IF_ID = LatchIFID(instr=fetched, pc=self.pc, predicted_taken=predicted_taken)
            pc_next = next_pc
        else:
            next_IF_ID = LatchIFID(instr=self.IF_ID.instr, pc=self.pc, predicted_taken=self.IF_ID.predicted_taken)
            pc_next = self.pc

        # Branch resolution/mispred in EX
        if next_EX_MEM.instr.op == "beq" and not next_EX_MEM.instr.is_nop:
            actual = next_EX_MEM.branch_taken
            predicted = next_EX_MEM.predicted_taken
            if actual:
                self.last_events["branch_taken"] = True
            if self.predictor_mode == "onebit":
                self.predictor_table[self.ID_EX.pc] = actual
            if actual != predicted:
                self.mispredicts += 1
                self.flushes += 1
                self.last_events["mispredict"] = True
                pc_next = next_EX_MEM.branch_target if actual else (self.ID_EX.pc + 1)
                next_IF_ID = LatchIFID(instr=NOP, pc=self.pc, predicted_taken=False)

        # Latch update
        self.MEM_WB = next_MEM_WB
        self.EX_MEM = next_EX_MEM
        self.ID_EX = next_ID_EX
        self.IF_ID = next_IF_ID
        self.pc = pc_next
        self.cycle += 1

        # x0 stays zero
        self.regs[0] = 0

        # Update last_stage annotations for visible PCs this cycle
        for st, pcv in pcs_start.items():
            if pcv is not None and pcv in self.inst_status:
                self.inst_status[pcv]["last_stage"] = st

        # Snapshot for simple stage text & VIZ
        snap = {
            "IF": ins_str(self.IF_ID.instr),
            "ID": ins_str(self.ID_EX.instr),
            "EX": ins_str(self.EX_MEM.instr),
            "MEM": ins_str(self.MEM_WB.instr),
            "WB": ins_str(self.MEM_WB.instr),
        }
        self.trace.append(snap.copy())
        return snap
