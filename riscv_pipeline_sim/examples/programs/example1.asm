# Simple demo program showing ALU forwarding, lw-use stall, and a taken branch.
# x0 is always zero.
add x1, x0, x0         # x1 = 0
add x2, x1, x1         # hazard vs prior; solvable via forwarding
lw  x3, 0(x1)          # load from dmem[0] (default 0 unless you write)
add x4, x3, x2         # lw-use -> requires single-cycle stall
beq x4, x0, SKIP       # not equal? falls through
add x5, x4, x4         # will be flushed if branch is taken
SKIP:
sw  x5, 4(x1)          # store result
sub x6, x5, x1
