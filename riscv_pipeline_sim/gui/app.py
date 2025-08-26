import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Dict, List, Tuple
from core.instructions import parse_program
from core.pipeline import PipelineCPU, STAGES

# -------------------- Theme --------------------
DARK_BG   = "#0b1220"
FG        = "#e5e7eb"
FG_DIM    = "#94a3b8"

CARD_BG   = "#0f172a"
CARD_EDGE = "#1f2937"

STAGE_COLORS = {
    "IF":  "#4f46e5",
    "ID":  "#16a34a",
    "EX":  "#ca8a04",
    "MEM": "#7c3aed",
    "WB":  "#dc2626",
}
STAGE_FILLS = {
    "IF":  "#e0e7ff",
    "ID":  "#dcfce7",
    "EX":  "#fef3c7",
    "MEM": "#ede9fe",
    "WB":  "#fee2e2",
}

TIMELINE_MAX = 42
GANTT_MAX    = 80

# -------------------- Tooltip --------------------
class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._enter)
        widget.bind("<Leave>", self._leave)
    def _enter(self, _):
        if self.tip is not None: return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip = tw = tk.Toplevel(self.widget)
        tw.overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        ttk.Label(tw, text=self.text, padding=6, style="Tooltip.TLabel", justify="left").pack()
    def _leave(self, _):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# -------------------- App --------------------
class PipelineApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RISC-V 5-Stage Pipeline (Expo Ultra — Polished & Aligned)")

        self._apply_theme()

        self.cpu = PipelineCPU()
        self.autoplay = False
        self.autoplay_ms = 360

        # UI refs
        self.stage_boxes: Dict[str, int] = {}
        self.stage_texts: Dict[str, int] = {}
        self.stage_labels: Dict[str, int] = {}
        self.reg_labels: List[ttk.Label] = []
        self.forward_arrows: List[int] = []

        self.cycle_label = None
        self.event_label = None
        self.metrics_label = None
        self.fwd_label = None

        # switches
        self.forward_var   = tk.BooleanVar(value=True)
        self.struct_var    = tk.BooleanVar(value=False)
        self.bp_var        = tk.StringVar(value="none")
        self.hex_regs_var  = tk.BooleanVar(value=False)
        self.hex_mem_var   = tk.BooleanVar(value=False)

        # memory editor
        self.mem_addr_var = tk.StringVar(value="0")
        self.mem_val_var  = tk.StringVar(value="0")

        # canvas state
        self._last_stage_map = {k: "NOP" for k in STAGES}
        self.event_log: List[str] = []
        self.last_logged_cycle = 0

        # geometry state
        self.layout = {}
        self._sash_fixed = False  # ensure we set the split only once at startup

        self._build_ui()

        # sync CPU with UI switches
        self.cpu.forwarding     = self.forward_var.get()
        self.cpu.structural_on  = self.struct_var.get()
        self.cpu.predictor_mode = self.bp_var.get()

    # -------------------- Theme --------------------
    def _apply_theme(self):
        s = ttk.Style(self.root)
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure("TFrame", background=DARK_BG)
        s.configure("TLabel", background=DARK_BG, foreground=FG)
        s.configure("Dim.TLabel", background=DARK_BG, foreground=FG_DIM)
        s.configure("Title.TLabel", background=DARK_BG, foreground=FG, font=("Segoe UI", 19, "bold"))
        s.configure("Badge.TLabel", background="#1f2937", foreground=FG, padding=(8, 3))
        s.configure("Tooltip.TLabel", background="#111827", foreground=FG, relief="solid", borderwidth=1)
        s.configure("TButton", padding=(10, 5))
        s.configure("TCheckbutton", background=DARK_BG, foreground=FG)

    # -------------------- Layout --------------------
    def _build_ui(self):
        self.root.configure(bg=DARK_BG)
        self.root.geometry("1760x1020")

        # Ribbon
        ribbon = ttk.Frame(self.root, padding=10)
        ribbon.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(ribbon, text="Next Cycle", command=self.on_next).pack(side=tk.LEFT, padx=4)
        self.play_btn = ttk.Button(ribbon, text="Auto-Play ▶", command=self.on_toggle_play)
        self.play_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(ribbon, text="Reset", command=self.on_reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(ribbon, text="Load Sample", command=self.on_load_sample).pack(side=tk.LEFT, padx=4)

        ttk.Checkbutton(ribbon, text="Forwarding", variable=self.forward_var, command=self.on_forward_toggle).pack(side=tk.LEFT, padx=(14, 6))
        ttk.Checkbutton(ribbon, text="Structural Hazards", variable=self.struct_var, command=self.on_struct_toggle).pack(side=tk.LEFT, padx=6)
        ttk.Label(ribbon, text="Branch Predictor:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(12, 4))
        bp_menu = ttk.OptionMenu(ribbon, self.bp_var, "none", "none", "static_nt", "onebit", command=self.on_bp_change)
        bp_menu.pack(side=tk.LEFT, padx=2)
        ToolTip(bp_menu, "none: resolve in EX\nstatic_nt: always not-taken\nonebit: per-PC 1-bit history")

        ttk.Button(ribbon, text="Export CSV", command=self.on_export_csv).pack(side=tk.RIGHT, padx=6)
        ttk.Button(ribbon, text="Help / Legend", command=self.open_help).pack(side=tk.RIGHT, padx=6)

        self.cycle_label = ttk.Label(ribbon, text="Cycle: 0", style="Badge.TLabel")
        self.cycle_label.pack(side=tk.LEFT, padx=16)
        self.event_label = ttk.Label(ribbon, text="", style="Badge.TLabel")
        self.event_label.pack(side=tk.LEFT, padx=8)
        self.metrics_label = ttk.Label(
            ribbon, text="Retired: 0 | Stalls: 0 | Flushes: 0 | Mispredicts: 0 | CPI: –",
            style="Badge.TLabel"
        )
        self.metrics_label.pack(side=tk.LEFT, padx=8)

        # Splitter
        self.paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # Canvas left
        self.canvas_wrap = ttk.Frame(self.paned)
        self.paned.add(self.canvas_wrap, weight=3)
        self.canvas = tk.Canvas(self.canvas_wrap, bg=DARK_BG, highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.fwd_label = ttk.Label(self.canvas_wrap, text="FWD A: none | FWD B: none", style="Dim.TLabel")
        self.fwd_label.pack(side=tk.BOTTOM, anchor="w", padx=16, pady=6)

        # Notebook right
        self.right_wrap = ttk.Frame(self.paned, width=640)
        self.paned.add(self.right_wrap, weight=2)

        # Pane minimums so right pane cannot swallow the canvas
        try:
            self.paned.paneconfigure(self.canvas_wrap, minsize=720)
            self.paned.paneconfigure(self.right_wrap,  minsize=420)
        except Exception:
            pass

        # Set initial sash position reliably (once), after geometry exists
        self.root.after_idle(self._ensure_initial_split)
        self.root.bind("<Configure>", self._ensure_initial_split_once)

        self.nb = ttk.Notebook(self.right_wrap)
        self.nb.pack(fill=tk.BOTH, expand=True)

        # Program
        prog_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(prog_tab, text="Program")
        ttk.Label(prog_tab, text="Program (RISC-V subset)").pack(anchor="w")
        self.text = tk.Text(prog_tab, width=60, height=16, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.text.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        ttk.Button(prog_tab, text="Load Instructions", command=self.on_load_text).pack(fill=tk.X)

        # Registers
        regs_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(regs_tab, text="Registers")
        ttk.Checkbutton(regs_tab, text="Show hex", variable=self.hex_regs_var, command=self._update_gui_from_cpu).pack(anchor="w")
        regs_frame = ttk.Frame(regs_tab)
        regs_frame.pack(fill=tk.BOTH, expand=True)
        self.reg_labels = []
        for i in range(32):
            lbl = ttk.Label(regs_frame, text=f"x{i:02d} = 0", width=28, anchor="w")
            lbl.grid(row=i//2, column=i%2, sticky="w", padx=6, pady=2)
            self.reg_labels.append(lbl)

        # Pipeline Regs
        pl_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(pl_tab, text="Pipeline Regs")
        self.pl_text = tk.Text(pl_tab, width=60, height=18, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.pl_text.pack(fill=tk.BOTH, expand=True)

        # Control Signals
        ctrl_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(ctrl_tab, text="Control Signals")
        self.ctrl_text = tk.Text(ctrl_tab, width=60, height=16, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.ctrl_text.pack(fill=tk.BOTH, expand=True)

        # Hazards/Dataflow
        haz_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(haz_tab, text="Hazards & Dataflow")
        self.haz_text = tk.Text(haz_tab, width=60, height=16, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.haz_text.pack(fill=tk.BOTH, expand=True)

        # Branch Predictor
        pred_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(pred_tab, text="Branch Predictor")
        self.pred_text = tk.Text(pred_tab, width=60, height=16, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.pred_text.pack(fill=tk.BOTH, expand=True)

        # Metrics
        met_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(met_tab, text="Metrics")
        self.metrics_canvas = tk.Canvas(met_tab, width=520, height=160, bg=DARK_BG, highlightthickness=0)
        self.metrics_canvas.pack(fill=tk.X, pady=(4, 8))
        self.metrics_caption = ttk.Label(met_tab, text="", style="Dim.TLabel")
        self.metrics_caption.pack(anchor="w")
        ttk.Label(met_tab, text="Stall breakdown: (reason → count)", style="Dim.TLabel").pack(anchor="w", pady=(6, 2))
        self.stall_text = tk.Text(met_tab, width=60, height=8, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.stall_text.pack(fill=tk.BOTH, expand=False)

        # Memory
        mem_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(mem_tab, text="Memory")
        self.hex_mem_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mem_tab, text="Show hex", variable=self.hex_mem_var, command=self.refresh_memory_view).pack(anchor="w")
        mem_top = ttk.Frame(mem_tab)
        mem_top.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(mem_top, text="Addr:").pack(side=tk.LEFT)
        tk.Entry(mem_top, textvariable=self.mem_addr_var, width=10, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat").pack(side=tk.LEFT, padx=6)
        ttk.Label(mem_top, text="Val:").pack(side=tk.LEFT)
        tk.Entry(mem_top, textvariable=self.mem_val_var, width=10, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat").pack(side=tk.LEFT, padx=6)
        ttk.Button(mem_top, text="Set (word)", command=self.on_mem_set).pack(side=tk.LEFT, padx=8)
        ttk.Button(mem_top, text="Refresh", command=self.refresh_memory_view).pack(side=tk.LEFT, padx=6)
        self.mem_text = tk.Text(mem_tab, width=60, height=14, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.mem_text.pack(fill=tk.BOTH, expand=True)

        # Program Status
        stat_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(stat_tab, text="Program Status")
        self.status_text = tk.Text(stat_tab, width=60, height=18, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.status_text.pack(fill=tk.BOTH, expand=True)

        # Pipeline Map
        gantt_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(gantt_tab, text="Pipeline Map")
        self.gantt_canvas = tk.Canvas(gantt_tab, width=660, height=420, bg=DARK_BG, highlightthickness=0)
        self.gantt_canvas.pack(fill=tk.BOTH, expand=True)

        # Legend
        legend_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(legend_tab, text="Legend & Notes")
        self.legend_text = tk.Text(legend_tab, width=60, height=22, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        self.legend_text.pack(fill=tk.BOTH, expand=True)

        # Draw canvas frame + first paint
        self._redraw_canvas_static()
        self._fill_default_program()
        self._populate_legend_text()
        self._update_gui_from_cpu()

    # --- initial split helpers ---
    def _ensure_initial_split(self, *_):
        if self._sash_fixed:
            return
        self.root.update_idletasks()
        total = self.paned.winfo_width()
        if total <= 1:
            # try again shortly until geometry is ready
            self.root.after(50, self._ensure_initial_split)
            return
        desired = int(total * 0.64)  # ~64% canvas, 36% right panel
        try:
            self.paned.sashpos(0, desired)
        except Exception:
            pass
        self._sash_fixed = True

    def _ensure_initial_split_once(self, _evt):
        # One-time guard in case some platforms ignore the after_idle call
        if not self._sash_fixed:
            self._ensure_initial_split()

    # -------------------- Canvas --------------------
    def _on_canvas_resize(self, _):
        self._redraw_canvas_static()
        self._update_gui_from_cpu(self._last_stage_map)

    def _compute_layout(self) -> Dict[str, int]:
        cw = max(1100, self.canvas.winfo_width())
        ch = max(700,  self.canvas.winfo_height())

        left_pad  = 28
        right_pad = 28
        top_pad   = 160  # roomy for title + 3 lines

        usable_w = cw - left_pad - right_pad

        # Stage card across full width
        N = 5
        box_w = min(260, max(170, int(usable_w * 0.16)))
        gap   = max(24, int((usable_w - N * box_w) / (N - 1)))
        if gap < 24:
            box_w = int((usable_w - (N - 1) * 24) / N)
            gap   = 24
        box_h = 118

        # Cards grid
        grid_gap    = 28
        left_col_w  = int(usable_w * 0.60)
        right_col_w = usable_w - left_col_w - grid_gap
        left_col_x  = left_pad
        right_col_x = left_pad + left_col_w + grid_gap

        ctrl_h   = 58
        stage_card_h = box_h + 116    # box area + captions/ribbon caption
        row1_top = top_pad
        row1_bot = row1_top + stage_card_h
        row2_top = row1_bot + ctrl_h + 36     # heatmap + perf row
        row3_top = row2_top + 228 + 36        # utilization + hazard row
        row4_top = row3_top + 198 + 36        # event log

        return {
            "left_pad": left_pad, "right_pad": right_pad, "top_pad": top_pad,
            "box_w": box_w, "box_h": box_h, "gap": gap,
            "ctrl_h": ctrl_h,
            "stage_card_h": stage_card_h,
            "grid_gap": grid_gap,
            "left_col_x": left_col_x, "left_col_w": left_col_w,
            "right_col_x": right_col_x, "right_col_w": right_col_w,
            "row1_top": row1_top, "row1_bot": row1_bot,
            "row2_top": row2_top, "row3_top": row3_top, "row4_top": row4_top,
            "canvas_w": cw, "canvas_h": ch
        }

    def _redraw_canvas_static(self):
        L = self._compute_layout()
        self.layout = L
        c = self.canvas
        c.delete("all")

        # Title + overview (spaced)
        c.create_text(L["left_pad"], 22, anchor="w",
                      text="Design & Implementation of a Visual Pipeline Simulator for a RISC-V 5-Stage CPU",
                      fill=FG, font=("Segoe UI", 19, "bold"))
        lines = [
            "Animates instructions through IF→ID→EX→MEM→WB each cycle to reveal overlapped execution.",
            "Highlights data/control/structural hazards, stalls, and forwarding paths. Branches may be predicted or resolved.",
            "Live metrics (CPI/IPC), Gantt view, and register/memory panels turn micro-ops into visible, debuggable behavior."
        ]
        c.create_text(L["left_pad"], 50, anchor="w", text=lines[0], fill=FG_DIM, font=("Segoe UI", 10))
        c.create_text(L["left_pad"], 70, anchor="w", text=lines[1], fill=FG_DIM, font=("Segoe UI", 10))
        c.create_text(L["left_pad"], 90, anchor="w", text=lines[2], fill=FG_DIM, font=("Segoe UI", 10))

        # Stage card with border + caption
        x, y, w, h = L["left_pad"], L["row1_top"], L["canvas_w"] - L["left_pad"] - L["right_pad"], L["stage_card_h"]
        self._card(x, y, w, h, title="Pipeline flow",
                   subtitle="Five pipeline stages with live instruction text; arrows show flow. Colors match all visuals below.")
        # stage boxes inside the card
        self.stage_boxes.clear(); self.stage_texts.clear(); self.stage_labels.clear()
        boxes_y = y + 64 
        for i, stage in enumerate(STAGES):
            bx = x + 12 + i*(L["box_w"] + L["gap"])
            rect = c.create_rectangle(bx, boxes_y, bx+L["box_w"], boxes_y+L["box_h"],
                                      fill=STAGE_FILLS[stage], outline=STAGE_COLORS[stage], width=3)
            self.stage_boxes[stage] = rect
            self.stage_labels[stage] = c.create_text(bx + L["box_w"]/2, boxes_y + 8, text=stage, fill=FG, font=("Segoe UI", 12, "bold"))
            self.stage_texts[stage]  = c.create_text(bx + 10, boxes_y + 20, anchor="w", text="NOP", fill="#111827", font=("Consolas", 12))

        # connectors
        for i in range(len(STAGES)-1):
            a = STAGES[i]; b = STAGES[i+1]
            ax1, ay1, ax2, ay2 = c.coords(self.stage_boxes[a])
            bx1, by1, bx2, by2 = c.coords(self.stage_boxes[b])
            ayc = (ay1 + ay2) / 2
            c.create_line(ax2 + 10, ayc, bx1 - 10, ayc, fill=FG_DIM, width=2, arrow=tk.LAST)

        # Control ribbon caption (separate from badges), safely below the stage card
        c.create_text(x + 12, y + h + 16, anchor="w",
                      text="Control (ID) — Generates per-instruction control signals (e.g., ALUOp, MemRead, RegWrite) that steer EX/MEM/WB.",
                      fill=FG_DIM, font=("Segoe UI", 9))

        # Row 2 cards: heatmap (left) + performance (right)
        self._card(L["left_col_x"], L["row2_top"], L["left_col_w"], 228,
                   title="Timeline heatmap",
                   subtitle="Which stage was active in each recent cycle. Colors match stage boxes.")
        self._card(L["right_col_x"], L["row2_top"], L["right_col_w"], 228,
                   title="On-chip performance",
                   subtitle="Left: IPC gauge (avg retired/cycle). Right: last few entries in the branch predictor.")

        # Row 3 cards: utilization (left) + hazard explainer (right)
        self._card(L["left_col_x"], L["row3_top"], L["left_col_w"], 198,
                   title="Stage utilization",
                   subtitle="Fraction of cycles each pipeline stage was busy since reset.")
        self._card(L["right_col_x"], L["row3_top"], L["right_col_w"], 198,
                   title="Hazard explainer",
                   subtitle="Explains stalls/flushes this cycle and shows producer → consumer dependency.")

        # Row 4 card: event log (full width)
        self._card(L["left_pad"], L["row4_top"], L["canvas_w"] - L["left_pad"] - L["right_pad"], 150,
                   title="Event log",
                   subtitle="Cycle-by-cycle summary: stalls, forwarding, branches, mispredictions, structural conflicts.")

    def _card(self, x, y, w, h, title="", subtitle=""):
        self.canvas.create_rectangle(x, y, x+w, y+h, fill=CARD_BG, outline=CARD_EDGE, width=1)
        if title:
            self.canvas.create_text(x+12, y+10, anchor="nw", text=title, fill=FG, font=("Segoe UI", 11, "bold"))
        if subtitle:
            self.canvas.create_text(x+12, y+30, anchor="nw", text=subtitle, fill=FG_DIM, font=("Segoe UI", 9))

    # -------------------- Data → UI --------------------
    def _update_gui_from_cpu(self, stage_map: Dict[str, str] = None):
        if stage_map is None:
            stage_map = {k: "NOP" for k in STAGES}
        self._last_stage_map = stage_map

        # stage texts + outlines
        for stg, txt_id in self.stage_texts.items():
            self.canvas.itemconfigure(txt_id, text=stage_map.get(stg, "NOP")[:64])
        for stg, rid in self.stage_boxes.items():
            self.canvas.itemconfigure(rid, outline=STAGE_COLORS[stg], width=3)

        # events → badges & pulses
        e = self.cpu.last_events
        badges = []
        if e.get("stall"):
            badges.append(f"STALL[{e.get('stall_reason','')}]"); self._pulse_outline("ID", "#b45309")
        if e.get("branch_taken"):
            badges.append("BRANCH TAKEN"); self._pulse_outline("IF", "#dc2626")
        if e.get("mispredict"):
            badges.append("MISPREDICT → flush IF/ID"); self._pulse_outline("IF", "#dc2626")
        if e.get("structural_stall"):
            badges.append("STRUCTURAL (IF↔MEM)"); self._pulse_outline("MEM", "#2563eb")
        self.event_label.config(text="  ".join(badges) if badges else "OK")

        # forwarding arrows + label
        self._draw_forwarding_arrows(e.get("fwd_a", "none"), e.get("fwd_b", "none"))
        self.fwd_label.config(text=f"FWD A: {e.get('fwd_a','none')} | FWD B: {e.get('fwd_b','none')}")

        # scoreboard
        ret = self.cpu.retired
        cyc = self.cpu.cycle
        cpi_txt = f"{(cyc/ret):.2f}" if ret else "–"
        self.cycle_label.config(text=f"Cycle: {cyc}")
        self.metrics_label.config(
            text=f"Retired: {ret} | Stalls: {self.cpu.stalls} | Flushes: {self.cpu.flushes} | "
                 f"Mispredicts: {self.cpu.mispredicts} | CPI: {cpi_txt}"
        )

        # right-panel textboxes
        snaps = self.cpu.get_latches_snapshot()
        self._fill_textbox(self.pl_text, self._format_latches(snaps))
        ctrl = self.cpu.get_control_snapshot()
        self._fill_textbox(self.ctrl_text, self._format_control(ctrl))
        self._fill_textbox(self.haz_text, self._format_hazards_and_flow(e, self.cpu.get_inflight()))
        self._fill_textbox(self.pred_text, self._format_predictor(self.cpu.get_predictor_snapshot()))
        self.refresh_memory_view()
        self._fill_textbox(self.status_text, self._format_program_status(self.cpu.get_program_status()))

        # canvas sections
        self._draw_control_ribbon(ctrl)
        self._update_heatmap()
        self._draw_performance_panel()
        self._draw_utilization_bars()
        self._draw_hazard_panel()
        self._maybe_append_event_log()
        self._draw_event_ticker()

        # metrics + gantt on tabs
        self._draw_metrics_chart()
        self._fill_textbox(self.stall_text, self._format_stall_breakdown(self.cpu.get_stall_breakdown()))
        self._draw_gantt()

    # -------------------- Formatters --------------------
    def _format_program_status(self, rows):
        lines = ["PC  | Stage | Ret | RetCycle | Instruction",
                 "----+-------+-----+----------+---------------------------"]
        for pc, instr, st, ret, rc in rows:
            rc_txt = "-" if rc is None else str(rc)
            lines.append(f"{pc:3d} | {st:^5} | {'Y' if ret else 'N'}   | {rc_txt:>8} | {instr}")
        return "\n".join(lines)

    def _format_latches(self, snaps: Dict[str, Dict[str, any]]) -> str:
        lines = []
        for name in ["IF/ID", "ID/EX", "EX/MEM", "MEM/WB"]:
            lines.append(f"{name}:")
            for k, v in snaps[name].items():
                lines.append(f"  {k:<6}: {v}")
            lines.append("")
        return "\n".join(lines)

    def _format_control(self, ctrl: Dict[str, any]) -> str:
        return (
            "Control (generated in ID):\n"
            f"  RegWrite : {ctrl['RegWrite']}\n"
            f"  MemRead  : {ctrl['MemRead']}\n"
            f"  MemWrite : {ctrl['MemWrite']}\n"
            f"  MemToReg : {ctrl['MemToReg']}\n"
            f"  Branch   : {ctrl['Branch']}\n"
            f"  ALUSrc   : {ctrl['ALUSrc']}\n"
            f"  ALUOp    : {ctrl['ALUOp']}\n"
        )

    def _format_hazards_and_flow(self, ev: Dict[str, any], inflight: List[Dict[str, any]]) -> str:
        lines = ["In-flight instructions:"]
        for row in inflight:
            s = f"  {row['stage']:<6} : {row['instr']}"
            if 'rd' in row: s += f"  rd=x{row['rd']}"
            if 'rs1' in row or 'rs2' in row: s += f"  uses=[x{row.get('rs1','-')}, x{row.get('rs2','-')}]"
            lines.append(s)
        lines.append("")
        if ev.get("stall"):
            det = ev.get("hazard_detail", {})
            lines += [
                "Stall analysis:",
                f"  reason     : {ev.get('stall_reason','')}",
                f"  producer   : pc={det.get('producer_pc')} op={det.get('producer_op','')} rd=x{det.get('producer_rd')}",
                f"  consumer   : op={det.get('consumer_op','')} uses={det.get('uses',[])}",
                "  note       : lw-use stalls 1 cycle even with forwarding (data ready after MEM)."
            ]
        else:
            lines.append("No decode stall this cycle.")
        return "\n".join(lines)

    def _format_predictor(self, rows: List[Tuple[int, str, str]]) -> str:
        if not rows:
            return "Predictor table is empty (either mode=none or no branches seen yet)."
        lines = ["PC   | State | Instruction", "-----+-------+----------------"]
        for pc, instr, state in rows:
            lines.append(f"{pc:4d} | {state:^5} | {instr}")
        lines.append("\nState: T=Taken last time, NT=Not taken last time.")
        return "\n".join(lines)

    def _format_stall_breakdown(self, rows: List[Tuple[str, int]]) -> str:
        if not rows: return "(no stalls yet)"
        return "\n".join([f"{k:<12} → {v}" for k, v in rows])

    def _fill_textbox(self, widget: tk.Text, text: str):
        widget.config(state="normal"); widget.delete("1.0", "end"); widget.insert("end", text); widget.config(state="disabled")

    # -------------------- Right-tab visuals --------------------
    def _draw_metrics_chart(self):
        m = self.cpu.get_cpi_breakdown()
        cv = self.metrics_canvas
        cv.delete("all")
        w, h, pad = 500, 140, 16
        x0, y0 = 10, 10
        cv.create_rectangle(x0, y0, x0+w, y0+h, outline=CARD_EDGE, fill=DARK_BG)
        bars = [("Useful %", m["useful_pct"], "#16a34a"),
                ("Stall %",  m["stall_pct"],  "#b45309"),
                ("Flush %",  m["flush_pct"],  "#dc2626")]
        bw = (w - 4*pad) / 3
        for i, (name, pct, color) in enumerate(bars):
            bx = x0 + pad + i*(bw + pad)
            by = y0 + h - pad
            bh = (h - 3*pad) * (min(max(pct, 0.0), 100.0) / 100.0)
            cv.create_rectangle(bx, by - bh, bx + bw, by, fill=color, outline="")
            cv.create_text(bx + bw/2, by + 12, text=f"{name} ({pct:.1f}%)", fill=FG_DIM, font=("Segoe UI", 9))
        self.metrics_caption.config(text=f"Cycles={m['cycles']}  |  Mispredicts={m['mispredicts']}")

    def _draw_gantt(self):
        cycles, row_labels, mat = self.cpu.get_gantt_window(max_cycles=GANTT_MAX)
        cv = self.gantt_canvas
        cv.delete("all")
        if not cycles:
            cv.create_text(20, 20, anchor="w", text="(no cycles yet)", fill=FG_DIM); return
        row_h, col_w = 18, 14
        left_w, top_h = 420, 28
        W = left_w + len(cycles)*col_w + 40
        H = top_h + len(row_labels)*row_h + 40
        cv.config(scrollregion=(0,0,W,H))
        cv.create_text(10, 10, anchor="w", text="Instruction (PC) →", fill=FG_DIM, font=("Segoe UI", 10))
        for j, c in enumerate(cycles):
            x = left_w + j*col_w + col_w/2
            cv.create_text(x, top_h-10, text=str(c%100), fill=FG_DIM, font=("Segoe UI", 8))
        for i, label in enumerate(row_labels):
            y = top_h + i*row_h + row_h/2
            cv.create_text(8, y, anchor="w", text=f"[{i:02d}] {label}", fill=FG, font=("Consolas", 9))
            for j, ch in enumerate(mat[i]):
                x1 = left_w + j*col_w; y1 = top_h + i*row_h
                x2 = x1 + col_w - 1;   y2 = y1 + row_h - 2
                fill = "#1f2937"
                if ch == "I": fill = STAGE_FILLS["IF"]
                elif ch == "D": fill = STAGE_FILLS["ID"]
                elif ch == "E": fill = STAGE_FILLS["EX"]
                elif ch == "M": fill = STAGE_FILLS["MEM"]
                elif ch == "W": fill = STAGE_FILLS["WB"]
                cv.create_rectangle(x1, y1, x2, y2, fill=fill, outline="#111")
                if ch != ".": cv.create_text((x1+x2)//2, (y1+y2)//2, text=ch, fill="#111", font=("Segoe UI", 8))

    # -------------------- Canvas visuals --------------------
    def _update_heatmap(self):
        L = self.layout
        ox = L["left_col_x"] + 16; oy = L["row2_top"] + 58
        cw, ch = 22, 18
        # labels
        for r, stage in enumerate(STAGES):
            y = oy + r*ch + ch/2
            self.canvas.create_text(ox - 14, y, anchor="e", text=stage, fill=FG_DIM, font=("Segoe UI", 9))
        # cells
        trace = self.cpu.trace[-TIMELINE_MAX:]
        for c, snap in enumerate(trace):
            for r, stage in enumerate(STAGES):
                instr = snap.get(stage, "NOP")
                color = STAGE_COLORS[stage] if instr and instr != "NOP" else "#1f2937"
                x1 = ox + c*cw; y1 = oy + r*ch
                self.canvas.create_rectangle(x1, y1, x1+cw-2, y1+ch-2, fill=color, outline="")

    def _draw_performance_panel(self):
        L = self.layout
        base_x = L["right_col_x"] + 16
        base_y = L["row2_top"] + 58
        w = L["right_col_w"] - 32

        # IPC gauge (left)
        cx = base_x + int(w*0.30)
        cy = base_y + 74
        r  = 64
        self.canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=180, extent=180, style="arc",
                               outline=CARD_EDGE, width=10)
        import math
        for i in range(0, 11):
            ang = 180 + i*18
            rad = math.radians(ang)
            x1 = cx + (r-14)*math.cos(rad); y1 = cy + (r-14)*math.sin(rad)
            x2 = cx + (r-4)*math.cos(rad);  y2 = cy + (r-4)*math.sin(rad)
            self.canvas.create_line(x1, y1, x2, y2, fill="#374151", width=2)
        cyc = max(1, self.cpu.cycle)
        ipc_val = (self.cpu.retired / cyc) if cyc else 0.0
        ipc = max(0.0, min(1.0, ipc_val))
        ang = 180 + ipc*180
        rad = math.radians(ang)
        nx = cx + (r-18)*math.cos(rad); ny = cy + (r-18)*math.sin(rad)
        self.canvas.create_line(cx, cy, nx, ny, fill="#22c55e", width=4)
        self.canvas.create_text(cx, cy+20, text=f"IPC≈{ipc_val:.2f}", fill=FG, font=("Segoe UI", 10))

        # Predictor table (right)
        px = base_x + int(w*0.52)
        py = base_y - 4
        self.canvas.create_text(px, py, anchor="nw", text="PC | State | Instr", fill=FG_DIM, font=("Segoe UI", 9, "bold"))
        py += 18
        rows = self.cpu.get_predictor_snapshot()[-6:]
        if not rows:
            self.canvas.create_text(px, py, anchor="nw", text="(empty)", fill=FG_DIM, font=("Segoe UI", 9))
        else:
            for pc, instr, state in rows:
                stc = "#22c55e" if state == "T" else "#eab308"
                self.canvas.create_text(px, py, anchor="nw", text=f"{pc:>3} | ", fill=FG, font=("Consolas", 9))
                self.canvas.create_text(px+38, py, anchor="nw", text=state, fill=stc, font=("Segoe UI", 9, "bold"))
                self.canvas.create_text(px+72, py, anchor="nw", text=instr[:18], fill=FG, font=("Consolas", 9))
                py += 16

    def _draw_utilization_bars(self):
        total = max(1, len(self.cpu.trace_stage_pc))
        counts = {s: 0 for s in STAGES}
        for pcs in self.cpu.trace_stage_pc:
            for s in STAGES:
                if pcs.get(s) is not None:
                    counts[s] += 1

        L = self.layout
        x0 = L["left_col_x"] + 16
        y0 = L["row3_top"] + 58
        bar_w = L["left_col_w"] - 160
        bar_h = 12; gap = 12
        for i, s in enumerate(STAGES):
            pct = counts[s] / total
            y = y0 + i*(bar_h+gap)
            self.canvas.create_text(x0, y-4, anchor="w", text=f"{s}", fill=FG_DIM, font=("Segoe UI", 9))
            self.canvas.create_rectangle(x0+26, y-6, x0+26+bar_w, y+6, outline=CARD_EDGE, fill="#0b152a")
            self.canvas.create_rectangle(x0+26, y-6, x0+26+int(bar_w*pct), y+6, outline="", fill=STAGE_COLORS[s])
            self.canvas.create_text(x0+26+bar_w+6, y, anchor="w", text=f"{pct*100:5.1f}%", fill=FG_DIM, font=("Segoe UI", 9))

    def _draw_hazard_panel(self):
        L = self.layout
        x = L["right_col_x"] + 16
        y = L["row3_top"] + 58
        e = self.cpu.last_events
        lines = []
        if e.get("stall"):
            d = e.get("hazard_detail", {})
            lines += [
                "Decode stall this cycle:",
                f"• Reason   : {e.get('stall_reason','')}",
                f"• Producer : pc={d.get('producer_pc')}  op={d.get('producer_op','')}  rd=x{d.get('producer_rd')}",
                f"• Consumer : op={d.get('consumer_op','')}  uses={d.get('uses',[])}",
                "• Note     : load-use needs 1 bubble even with forwarding (data @ MEM).",
            ]
        else:
            lines.append("No decode stall this cycle.")
        if e.get("branch_taken"):     lines.append("Branch resolved in EX: TAKEN.")
        if e.get("mispredict"):       lines.append("Mispredict: flushed IF/ID.")
        if e.get("structural_stall"): lines.append("Structural: IF conflicted with MEM (single port).")

        yy = y
        for ln in lines[:8]:
            self.canvas.create_text(x, yy, anchor="nw", text=ln, fill=FG, font=("Segoe UI", 10))
            yy += 18

    def _maybe_append_event_log(self):
        if self.cpu.cycle == self.last_logged_cycle: return
        self.last_logged_cycle = self.cpu.cycle
        e = self.cpu.last_events
        parts = [f"C{self.cpu.cycle:>3}:"]
        if e.get("stall"): parts.append(f"stall[{e.get('stall_reason','')}]")
        if e.get("branch_taken"): parts.append("branch_taken")
        if e.get("mispredict"): parts.append("mispredict")
        if e.get("structural_stall"): parts.append("structural")
        fa, fb = e.get("fwd_a","none"), e.get("fwd_b","none")
        if fa!="none" or fb!="none": parts.append(f"FWD A={fa},B={fb}")
        if len(parts)==1: parts.append("ok")
        self.event_log.append(" ".join(parts))
        self.event_log = self.event_log[-12:]

    def _draw_event_ticker(self):
        L = self.layout
        x = L["left_pad"] + 18
        y = L["row4_top"] + 56
        for line in self.event_log[-10:]:
            self.canvas.create_text(x, y, anchor="nw", text=line, fill=FG, font=("Consolas", 10))
            y += 14

    def _draw_control_ribbon(self, ctrl: Dict[str, any]):
        self.canvas.delete("ctrl_ribbon")
        boxes = [self.stage_boxes[s] for s in STAGES]
        x1 = self.canvas.coords(boxes[0])[0]
        x2 = self.canvas.coords(boxes[-1])[2]
        by = self.layout["row1_top"] + self.layout["stage_card_h"] - 24  # within stage card bottom
        bx = x1
        badges = [
            ("RegWrite", ctrl["RegWrite"]),
            ("MemRead",  ctrl["MemRead"]),
            ("MemWrite", ctrl["MemWrite"]),
            ("MemToReg", ctrl["MemToReg"]),
            ("Branch",   ctrl["Branch"]),
            ("ALUSrc:"+("imm" if ctrl["ALUSrc"]=="imm" else "reg"), ctrl["ALUSrc"]=="imm"),
            ("ALUOp:"+ctrl["ALUOp"], True),
        ]
        for label, on in badges:
            w = 96 if ":" in label else 86
            fill = "#14532d" if on else "#334155"
            self.canvas.create_rectangle(bx, by, bx+w, by+22, fill=fill, outline="", tags="ctrl_ribbon")
            self.canvas.create_text(bx+8, by+11, anchor="w", text=label, fill=FG, font=("Segoe UI", 9), tags="ctrl_ribbon")
            bx += w + 8
            if bx + w > x2:
                by += 24; bx = x1

    def _draw_forwarding_arrows(self, fwd_a: str, fwd_b: str):
        for aid in self.forward_arrows: self.canvas.delete(aid)
        self.forward_arrows.clear()
        def center(stage):
            x1, y1, x2, y2 = self.canvas.coords(self.stage_boxes[stage])
            return ((x1+x2)/2, (y1+y2)/2)
        def draw(src, dst, color):
            sx, sy = src; dx, dy = dst
            aid = self.canvas.create_line(sx, sy, dx, dy, fill=color, width=3, arrow=tk.LAST, smooth=True)
            self.forward_arrows.append(aid)
        if fwd_a in ("EX/MEM",) or fwd_b in ("EX/MEM",): draw(center("MEM"), center("EX"), "#22c55e")
        if fwd_a in ("MEM/WB",) or fwd_b in ("MEM/WB",): draw(center("WB"), center("EX"), "#60a5fa")

    def _pulse_outline(self, stage: str, color: str):
        box = self.stage_boxes.get(stage)
        if not box: return
        self.canvas.itemconfigure(box, outline=color, width=4)
        self.root.after(220, lambda: self.canvas.itemconfigure(box, outline=STAGE_COLORS[stage], width=3))

    # -------------------- Ribbon handlers --------------------
    def on_forward_toggle(self): self.cpu.forwarding = bool(self.forward_var.get())
    def on_struct_toggle(self):  self.cpu.structural_on = bool(self.struct_var.get())
    def on_bp_change(self, *_):  self.cpu.predictor_mode = self.bp_var.get()

    def on_next(self):
        stage_map = self.cpu.step()
        self._animate_update(stage_map)

    def on_toggle_play(self):
        self.autoplay = not self.autoplay
        self.play_btn.config(text=("Auto-Play ■" if self.autoplay else "Auto-Play ▶"))
        if self.autoplay: self._tick()

    def _tick(self):
        if not self.autoplay: return
        self.on_next()
        self.root.after(self.autoplay_ms, self._tick)

    def on_reset(self):
        self.cpu.reset()
        self.cpu.forwarding     = self.forward_var.get()
        self.cpu.structural_on  = self.struct_var.get()
        self.cpu.predictor_mode = self.bp_var.get()
        self.event_log.clear()
        self.last_logged_cycle = 0
        try:
            prog = parse_program(self.text.get("1.0", "end"))
            self.cpu.load_program(prog)
        except Exception:
            pass
        self._update_gui_from_cpu()

    def on_load_sample(self):
        try:
            with open("examples/programs/example1.asm", "r", encoding="utf-8") as f:
                self.text.delete("1.0", "end")
                self.text.insert("1.0", f.read())
        except Exception as e:
            messagebox.showerror("Error", f"Couldn't load sample: {e}")

    def on_load_text(self):
        try:
            prog = parse_program(self.text.get("1.0", "end"))
            self.cpu.load_program(prog)
            self.event_log.clear()
            self.last_logged_cycle = 0
            self._update_gui_from_cpu()
        except Exception as e:
            messagebox.showerror("Parse Error", str(e))

    def on_export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV files", "*.csv")],
                                            title="Export timeline CSV")
        if path:
            try:
                self.cpu.export_csv(path)
                messagebox.showinfo("Export", f"Saved: {path}")
            except Exception as e:
                messagebox.showerror("Export failed", str(e))

    # -------------------- Memory --------------------
    def on_mem_set(self):
        try:
            addr = int(self.mem_addr_var.get(), 0)
            val  = int(self.mem_val_var.get(), 0)
            self.cpu.dmem[addr] = val
            self.refresh_memory_view()
        except Exception as e:
            messagebox.showerror("Memory write failed", str(e))

    def refresh_memory_view(self):
        self.mem_text.config(state="normal")
        self.mem_text.delete("1.0", "end")
        items = sorted(self.cpu.dmem.items(), key=lambda kv: kv[0])
        if not items:
            self.mem_text.insert("end", "(memory is empty; lw reads default 0)\n")
        else:
            for a, v in items[:512]:
                if self.hex_mem_var.get():
                    self.mem_text.insert("end", f"[{a:04d}] = {v} (0x{(v & 0xffffffff):08x})\n")
                else:
                    self.mem_text.insert("end", f"[{a:04d}] = {v}\n")
        self.mem_text.config(state="disabled")

    # -------------------- Legend / Help --------------------
    def _populate_legend_text(self):
        t = self.legend_text
        t.config(state="normal"); t.delete("1.0", "end")
        t.insert("end",
                 "✦ Pipeline overview\n"
                 "• Five classic stages (IF/ID/EX/MEM/WB). Each cycle, instructions advance; NOP = bubble.\n"
                 "• Connectors show flow. Colored pulses: stall / branch / structural conflict. Arrows show forwarding paths.\n\n"
                 "✦ Hazards\n"
                 "• Data (RAW): solved by forwarding (EX/MEM or MEM/WB) when enabled.\n"
                 "• Load–use: 1-cycle stall; load data appears after MEM.\n"
                 "• Control: branches resolve in EX; mispredictions flush IF/ID. Predictor modes: none, static_nt, onebit.\n"
                 "• Structural: optional single-ported memory; IF conflicts with MEM on lw/sw.\n\n"
                 "✦ Teaching Panels\n"
                 "• Control Signals, Hazards & Dataflow, Branch Predictor, Metrics, Program Status, Pipeline Map.\n"
                 "• Canvas sections each include a one-line caption so students know what they’re looking at.\n")
        t.config(state="disabled")

    def open_help(self):
        win = tk.Toplevel(self.root)
        win.title("Quick Guide & Legend")
        win.configure(bg=DARK_BG)
        ttk.Label(win, text="RISC-V Pipeline — Quick Guide", style="Title.TLabel").pack(anchor="w", padx=14, pady=(14, 6))
        txt = tk.Text(win, width=100, height=30, bg=CARD_BG, fg=FG, insertbackground=FG, relief="flat")
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        txt.insert("end", self.legend_text.get("1.0", "end"))
        txt.config(state="disabled")

    # -------------------- Animation --------------------
    def _animate_update(self, stage_map):
        steps, dx = 9, 10
        orig_pos = {}
        for stg, txt_id in self.stage_texts.items():
            x, y = self.canvas.coords(txt_id)
            orig_pos[stg] = (x, y)
            self.canvas.coords(txt_id, x - dx*steps, y)
        def step_anim(k=0):
            if k >= steps:
                self._update_gui_from_cpu(stage_map)
                for stg, txt_id in self.stage_texts.items():
                    x, y = orig_pos[stg]; self.canvas.coords(txt_id, x, y)
                return
            for _, txt_id in self.stage_texts.items():
                self.canvas.move(txt_id, dx, 0)
            self.root.after(14, lambda: step_anim(k+1))
        for stg, txt_id in self.stage_texts.items():
            self.canvas.itemconfigure(txt_id, text=stage_map.get(stg, "NOP")[:64])
        step_anim()

    # -------------------- Defaults --------------------
    def _fill_default_program(self):
        self.text.insert("1.0",
            "add x1, x0, x0\n"
            "add x2, x1, x1\n"
            "lw x3, 0(x1)\n"
            "add x4, x3, x2\n"
            "beq x4, x0, SKIP\n"
            "add x5, x4, x4\n"
            "SKIP:\n"
            "sw x5, 4(x1)\n"
            "sub x6, x5, x1\n"
        )

    def run(self):
        self.root.mainloop()

# Entry
if __name__ == "__main__":
    PipelineApp().run()
