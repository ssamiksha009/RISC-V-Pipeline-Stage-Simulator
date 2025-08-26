# Visual Pipeline Simulator — RISC-V 5-Stage

**Run:** `python main.py`

**Deps:** Python 3.8+ (Tkinter included with most Python installs).

## Supported Instructions
- `add rd, rs1, rs2`
- `sub rd, rs1, rs2`
- `lw rd, offset(rs1)`
- `sw rs2, offset(rs1)`
- `beq rs1, rs2, label`
- `nop` (inserted automatically for stalls/bubbles; you can also write `nop` explicitly)

## Notes
- Classic 5-stage: IF, ID, EX, MEM, WB.
- Branch decision in EX; taken branches flush IF/ID and redirect PC.
- Data hazards:
  - With **Forwarding ON**: ALU→ALU forwarding (EX/MEM -> EX) and MEM/WB -> EX. `lw`-use requires a single-cycle stall.
  - With **Forwarding OFF**: conservative stalls when ID depends on pending results in EX/MEM/MEM/WB.
- Structural hazards are not modeled (assume split I/D memory).
- Use **Next Cycle** or **Auto-Play**. Load sample via **Load Sample** or paste your own assembly and click **Load Instructions**.

## Example Program
See `examples/programs/example1.asm`.
