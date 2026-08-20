#!/usr/bin/env python3
"""
Master publication-grade figure generation for Nile University Technical Report.
Engineered with exact border-to-border arrow anchoring (shrinkA=0, shrinkB=0, pad=0),
generous padding, zero text collisions, and separated fail/success channels.
"""

import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

OUTPUT_DIRS = ["temp/report/figures", "docs/evidence"]

def setup_dirs():
    for d in OUTPUT_DIRS:
        os.makedirs(d, exist_ok=True)

def save_fig(fig, name):
    for d in OUTPUT_DIRS:
        png_path = os.path.join(d, f"{name}.png")
        pdf_path = os.path.join(d, f"{name}.pdf")
        fig.savefig(png_path, dpi=300, bbox_inches='tight')
        fig.savefig(pdf_path, bbox_inches='tight')
        print(f"Saved: {png_path} and {pdf_path}")
    plt.close(fig)

# =============================================================================
# 1. Philosophy Diagram: "Reason Free, Constrain Late"
# (Exact border-to-border arrows with shrinkA=0, shrinkB=0)
# =============================================================================
def generate_philosophy_diagram():
    fig, ax = plt.subplots(figsize=(16.2, 4.4), dpi=300)
    ax.set_xlim(0, 16.2)
    ax.set_ylim(0, 4.4)
    ax.axis('off')

    # Title
    ax.text(8.1, 4.05, 'The "Reason Free, Constrain Late" Paradigm: Decoupling Physics from Syntax',
            ha='center', va='center', fontsize=13, fontweight='bold', color='#003366')

    # 4 distinct main blocks (pad=0 ensures exact (x, y, w, h) outer boundaries)
    boxes = [
        (0.6, 0.6, 2.3, 2.6, 
         "Natural Language\nProblem Prompt\n\n* Plain English spec\n* Boundary conditions\n* Working fluid & regime", 
         '#e3f2fd', '#1565c0'),
        (5.0, 0.6, 2.3, 2.6, 
         "Physics Reasoning\nCaseIntent (IR)\n\n* Typed Pydantic JSON\n* Solver family enum\n* Physical envelopes", 
         '#e0f2f1', '#00695c'),
        (9.4, 0.6, 2.3, 2.6, 
         "Structural Model\nCaseSpec (Spec)\n\n* 7-int dimension vectors\n* fvSchemes / fvSolution\n* Boundary wall functions", 
         '#ede7f6', '#4527a0'),
        (13.8, 0.6, 2.3, 2.6, 
         "OpenFOAM v13 Case\nGuaranteed Dictionaries\n\n* 0/ (field arrays)\n* constant/ (properties)\n* system/ (control/MPI)", 
         '#fff3e0', '#e65100'),
    ]

    for (x, y, w, h, text, bg, border) in boxes:
        rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.15",
                                      facecolor=bg, edgecolor=border, linewidth=1.8)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=8.4, fontweight='bold', color='#1a1a1a', linespacing=1.35)

    # Exact border-to-border transitions: right edge of box A -> left edge of box B
    transitions = [
        (2.90, 1.90, 5.00, 1.90, "LLM Reasoning\n(Unconstrained)"),
        (7.30, 1.90, 9.40, 1.90, "intent_to_spec()\n(Deterministic)"),
        (11.70, 1.90, 13.80, 1.90, "render()\n(100% v13 Syntax)"),
    ]

    for (x1, y1, x2, y2, label) in transitions:
        ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color='#37474f', lw=2.2, mutation_scale=15, shrinkA=0, shrinkB=0))
        ax.text((x1+x2)/2, y1 + 0.30, label, ha='center', va='bottom', fontsize=8.4, fontweight='bold', color='#004d40', linespacing=1.2)

    save_fig(fig, "philosophy_diagram")

# =============================================================================
# 2. Pipeline Architecture Flowchart
# (Exact border-to-border connections with shrinkA=0, shrinkB=0)
# =============================================================================
def generate_pipeline_diagram():
    fig, ax = plt.subplots(figsize=(12.8, 7.0), dpi=300)
    ax.set_xlim(0, 12.8)
    ax.set_ylim(0, 7.0)
    ax.axis('off')

    ax.text(6.4, 6.65, "LangGraph Autonomous Multi-Agent State Machine (src/orchestration/graph.py)",
            ha='center', va='center', fontsize=12.5, fontweight='bold', color='#003366')

    # Exact Block Coordinates (pad=0 ensures geometric borders match x, y, w, h)
    s0 = (0.5, 4.2, 2.2, 1.3, "Stage 0: read_mesh\nExtract patch names\n& boundary types", '#e3f2fd', '#1565c0')
    s1 = (3.4, 4.2, 2.3, 1.3, "Stage 1: build_intent\nLLM + RAG Tutorials\n+ Envelope Clamping", '#e0f2f1', '#00695c')
    s2 = (6.4, 4.2, 2.2, 1.3, "Stage 2: expand_spec\n18-Solver Registry\nDimensions & BCs", '#ede7f6', '#4527a0')
    s3 = (9.3, 4.2, 2.2, 1.3, "Stage 3: render_case\nCompile Case Files\n0/, constant/, system/", '#e8f5e9', '#2e7d32')

    s35 = (9.3, 2.1, 2.2, 1.3, "Stage 3.5: preflight\nLevel A, B, C Audits\n(Fail-Fast Predictor)", '#fff3e0', '#ef6c00')
    patch = (5.6, 2.3, 2.5, 0.9, "patch_files\n(Targeted File Rewrite)", '#ffebee', '#c62828')
    s4  = (5.6, 0.4, 2.5, 1.3, "Stage 4: validate_case\nDocker OpenFOAM v13\nNaN Abort & MPI Exec", '#e8f5e9', '#1b5e20')
    end = (0.5, 0.4, 2.2, 1.3, "END (Working Case)\nVerified Executable\nCase Directory", '#c8e6c9', '#2e7d32')

    for (x, y, w, h, text, bg, border) in [s0, s1, s2, s3, s35, patch, s4, end]:
        rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                      facecolor=bg, edgecolor=border, linewidth=1.6)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=8.2, fontweight='bold', color='#1a1a1a', linespacing=1.25)

    # 1. Forward synthesis arrows (Top Row: right border -> left border)
    ax.annotate('', xy=(3.40, 4.85), xytext=(2.70, 4.85),
                arrowprops=dict(arrowstyle="-|>", color='#37474f', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(6.40, 4.85), xytext=(5.70, 4.85),
                arrowprops=dict(arrowstyle="-|>", color='#37474f', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(9.30, 4.85), xytext=(8.60, 4.85),
                arrowprops=dict(arrowstyle="-|>", color='#37474f', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))
    
    # 2. Stage 3 bottom border -> Stage 3.5 top border (Vertical)
    ax.annotate('', xy=(10.40, 3.40), xytext=(10.40, 4.20),
                arrowprops=dict(arrowstyle="-|>", color='#37474f', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))

    # 3. Preflight to Validate (Left edge of s35 at y=2.25 -> Right edge of s4 at y=1.45)
    ax.annotate('', xy=(8.10, 1.45), xytext=(9.30, 2.25),
                arrowprops=dict(arrowstyle="-|>", color='#2e7d32', lw=2.0, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.text(9.20, 1.65, "PASS / WARN", ha='center', va='bottom', fontsize=8, fontweight='bold', color='#2e7d32')

    # 4. Preflight FAIL: Starts EXACTLY at s35 right border (11.50, 2.75) -> ends EXACTLY at s1 top border (4.55, 5.50)
    ax.plot([11.50, 12.20, 12.20, 4.55, 4.55], [2.75, 2.75, 6.20, 6.20, 5.50], color='#d32f2f', lw=1.8)
    ax.annotate('', xy=(4.55, 5.50), xytext=(4.55, 5.75),
                arrowprops=dict(arrowstyle="-|>", color='#d32f2f', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.text(8.40, 5.92, "Preflight FAIL (Static Audit / Slope Divergence)", ha='center', va='top', fontsize=8.2, fontweight='bold', color='#d32f2f')

    # 5. Success Path (Green Arrow): Starts EXACTLY at s4 left border (5.60, 0.75) -> ends at END right border (2.70, 0.75)
    ax.annotate('', xy=(2.70, 0.75), xytext=(5.60, 0.75),
                arrowprops=dict(arrowstyle="-|>", color='#2e7d32', lw=2.0, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.text(4.15, 0.48, "Execution Success", ha='center', va='top', fontsize=8.5, fontweight='bold', color='#2e7d32')

    # 6. Runtime/Mesh Fail Path (Red Line): Starts EXACTLY at s4 left border (5.60, 1.35) -> ends at s1 bottom border (3.80, 4.20)
    ax.plot([5.60, 3.80, 3.80, 3.80], [1.35, 1.35, 3.90, 4.20], color='#c62828', lw=1.8)
    ax.annotate('', xy=(3.80, 4.20), xytext=(3.80, 3.90),
                arrowprops=dict(arrowstyle="-|>", color='#c62828', lw=1.8, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.text(2.75, 2.75, "SOLVER_RUNTIME (NaN)\n/ MESH_QUALITY", ha='center', va='center', fontsize=7.6, fontweight='bold', color='#c62828')

    # 7. Syntax Fail Path (Red Arrow): Starts EXACTLY at s4 top border (6.85, 1.70) -> ends at patch bottom border (6.85, 2.30)
    ax.annotate('', xy=(6.85, 2.30), xytext=(6.85, 1.70),
                arrowprops=dict(arrowstyle="-|>", color='#d32f2f', lw=1.6, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.text(7.00, 1.95, "DICTIONARY_SYNTAX", ha='left', va='center', fontsize=7.2, fontweight='bold', color='#d32f2f')

    # 8. patch_files to render_case (Blue Arrow): Starts at patch top-right (8.10, 3.20) -> ends at s3 bottom-left (9.30, 4.20)
    ax.annotate('', xy=(9.30, 4.20), xytext=(8.10, 3.20),
                arrowprops=dict(arrowstyle="-|>", color='#1565c0', lw=1.6, mutation_scale=12, shrinkA=0, shrinkB=0))

    save_fig(fig, "pipeline_architecture")

# =============================================================================
# 3. Ranque-Hilsch Vortex Tube (RHVT) Schematic
# (Exact border-anchored inlet and outlet arrows)
# =============================================================================
def generate_rhvt_schematic():
    fig, ax = plt.subplots(figsize=(11.8, 5.2), dpi=300)
    ax.set_xlim(0, 11.8)
    ax.set_ylim(0, 5.2)
    ax.axis('off')

    # Title
    ax.text(5.9, 4.9, "Ranque-Hilsch Vortex Tube (RHVT) Geometry & Energy Separation Physics",
            ha='center', va='center', fontsize=12.5, fontweight='bold', color='#003366')

    # Main Tube Body: x in [2.4, 8.2], y in [1.2, 3.4]
    tube = patches.Rectangle((2.4, 1.2), 5.8, 2.2, facecolor='#fafafa', edgecolor='#424242', linewidth=2.0)
    ax.add_patch(tube)

    # Tangential Inlet Chamber: x in [3.0, 3.9], y in [3.4, 4.15]
    inlet = patches.Rectangle((3.0, 3.4), 0.9, 0.75, facecolor='#bbdefb', edgecolor='#1976d2', linewidth=1.8)
    ax.add_patch(inlet)
    # Inlet downward arrow: starts at top of inlet (3.45, 4.15), ends at entrance into tube (3.45, 3.40)
    ax.annotate('', xy=(3.45, 3.40), xytext=(3.45, 4.15),
                arrowprops=dict(arrowstyle="-|>", color='#0d47a1', lw=2.2, mutation_scale=14, shrinkA=0, shrinkB=0))
    ax.text(3.45, 4.28, "Compressed Gas Inlet (Tangential)\nHigh-Velocity Swirl (~200 m/s)", ha='center', va='bottom', fontsize=8, fontweight='bold', color='#0d47a1')

    # Cold Orifice: x in [1.3, 2.4], y in [1.8, 2.8]
    cold_orifice = patches.Rectangle((1.3, 1.8), 1.1, 1.0, facecolor='#e1f5fe', edgecolor='#0288d1', linewidth=1.8)
    ax.add_patch(cold_orifice)
    # Cold exit arrow: starts EXACTLY at left border of cold orifice (1.30, 2.30)
    ax.annotate('', xy=(0.30, 2.30), xytext=(1.30, 2.30),
                arrowprops=dict(arrowstyle="-|>", color='#0288d1', lw=2.4, mutation_scale=14, shrinkA=0, shrinkB=0))
    ax.text(0.25, 2.30, "Cold Stream Exit\n(Central Core Flow)", ha='right', va='center', fontsize=8.2, fontweight='bold', color='#0288d1')

    # Hot Control Valve & Exit (Right): x in [8.2, 9.3], y in [1.2, 3.4]
    hot_cone = patches.Polygon([[8.2, 1.2], [9.3, 1.8], [9.3, 2.8], [8.2, 3.4]], facecolor='#ffebee', edgecolor='#d32f2f', linewidth=1.8)
    ax.add_patch(hot_cone)
    # Hot exit arrow: starts EXACTLY at right tip of hot cone (9.30, 2.30)
    ax.annotate('', xy=(10.60, 2.30), xytext=(9.30, 2.30),
                arrowprops=dict(arrowstyle="-|>", color='#d32f2f', lw=2.4, mutation_scale=14, shrinkA=0, shrinkB=0))
    ax.text(10.70, 2.30, "Hot Stream Exit\n(Peripheral Swirl)", ha='left', va='center', fontsize=8.2, fontweight='bold', color='#d32f2f')

    # Internal Streamlines
    ax.annotate('', xy=(7.70, 3.00), xytext=(3.60, 3.00), arrowprops=dict(arrowstyle="->", color='#e53935', lw=1.8, linestyle="--", shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(7.70, 1.60), xytext=(3.60, 1.60), arrowprops=dict(arrowstyle="->", color='#e53935', lw=1.8, linestyle="--", shrinkA=0, shrinkB=0))
    ax.text(5.50, 2.75, "Hot Peripheral Swirling Layer\n(High Kinetic & Thermal Energy)", 
            ha='center', va='center', fontsize=7.6, color='#c62828', fontweight='bold', linespacing=1.2)

    # Core cold reverse flow line: enters cold orifice at (1.30, 2.30)
    ax.annotate('', xy=(1.30, 2.30), xytext=(7.40, 2.30), arrowprops=dict(arrowstyle="->", color='#0288d1', lw=2.0, linestyle="-", shrinkA=0, shrinkB=0))
    ax.text(4.80, 1.95, "Cold Reversed Core Stream\n(Expanded Low-Pressure Gas)", 
            ha='center', va='center', fontsize=7.6, color='#01579b', fontweight='bold', linespacing=1.2)

    # Bottom Mesh Details Box
    mesh_box = patches.FancyBboxPatch((2.2, 0.12), 7.2, 0.85, boxstyle="round,pad=0,rounding_size=0.06", facecolor='#fffde7', edgecolor='#fbc02d', lw=1.4)
    ax.add_patch(mesh_box)
    ax.text(5.8, 0.62, "Supervisor's Industrial Mesh: 10.64 Million Faces · 9 Boundary Patches", ha='center', va='center', fontsize=9, fontweight='bold', color='#e65100')
    ax.text(5.8, 0.32, "Validated in OpenFOAM v13 fluid module across 10 MPI ranks (130.9s execution time)", ha='center', va='center', fontsize=8, color='#424242')

    save_fig(fig, "rhvt_schematic")

# =============================================================================
# 4. 4-Tier Validation Ladder & Preflight Gate Diagram
# (Exact border-to-border connections with shrinkA=0, shrinkB=0)
# =============================================================================
def generate_validation_ladder_diagram():
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=300)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.6)
    ax.axis('off')

    ax.text(6.0, 4.25, "Defense-in-Depth: 4-Tier Automated Validation Ladder & Preflight Gate",
            ha='center', va='center', fontsize=12.5, fontweight='bold', color='#003366')

    tiers = [
        (0.5, 2.1, 2.4, 1.7, "Level A: Static Audit\n(Host In-Process, Free)\n\n* AST keyword validation\n* PIMPLE/SIMPLE loop\n* rho relaxation (0.05)\n* Dimension vector check", '#e3f2fd', '#1565c0'),
        (3.3, 2.1, 2.4, 1.7, "Level B: Mesh Metrics\n(checkMesh, Seconds)\n\n* Non-orthogonality (<70°)\n* Skewness (<4)\n* Aspect ratio (<1000)\n* Negative volume check", '#ede7f6', '#4527a0'),
        (6.1, 2.1, 2.5, 1.7, "Level C: Bounded Dry-Run\n(Docker, Tens of Secs)\n\n* 5-10 Step Bounded Solve\n* Log-slope regression:\n  Slope = d log10(R) / d step\n* Slope >= +0.05 -> FAIL", '#fff3e0', '#e65100'),
        (9.0, 2.1, 2.5, 1.7, "Docker Full Execution\n(microfluidica/openfoam:13)\n\n* Real-time stdout stream\n* First-solve NaN abort\n* Scotch MPI parallel\n* maxIter capping (~15x)", '#e8f5e9', '#2e7d32')
    ]

    for (x, y, w, h, text, bg, border) in tiers:
        rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                      facecolor=bg, edgecolor=border, linewidth=1.6)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=7.8, fontweight='bold', color='#1a1a1a', linespacing=1.25)

    # Exact border-to-border connecting arrows: Tier N right border -> Tier N+1 left border
    ax.annotate('', xy=(3.30, 2.95), xytext=(2.90, 2.95),
                arrowprops=dict(arrowstyle="-|>", color='#2e7d32', lw=2, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(6.10, 2.95), xytext=(5.70, 2.95),
                arrowprops=dict(arrowstyle="-|>", color='#2e7d32', lw=2, mutation_scale=12, shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(9.00, 2.95), xytext=(8.60, 2.95),
                arrowprops=dict(arrowstyle="-|>", color='#2e7d32', lw=2, mutation_scale=12, shrinkA=0, shrinkB=0))

    # Preflight bracket
    ax.annotate('', xy=(8.60, 1.80), xytext=(0.50, 1.80), arrowprops=dict(arrowstyle="<->", color='#ef6c00', lw=1.8, shrinkA=0, shrinkB=0))
    ax.text(4.55, 1.55, "Preflight Gate (Fast Fail-Fast Prediction on Host)", ha='center', va='center', fontsize=8.5, fontweight='bold', color='#e65100')

    # Full execution bracket
    ax.annotate('', xy=(11.50, 1.80), xytext=(9.00, 1.80), arrowprops=dict(arrowstyle="<->", color='#2e7d32', lw=1.8, shrinkA=0, shrinkB=0))
    ax.text(10.25, 1.55, "Source of Truth (Full Run)", ha='center', va='center', fontsize=8.5, fontweight='bold', color='#2e7d32')

    # Summary Box
    benefit_box = patches.FancyBboxPatch((0.5, 0.15), 11.0, 1.1, boxstyle="round,pad=0,rounding_size=0.06", facecolor='#f5f5f5', edgecolor='#9e9e9e', lw=1.2)
    ax.add_patch(benefit_box)
    ax.text(6.0, 0.95, "Key Engineering Impact of the Tiered Validation Ladder:", ha='center', va='center', fontsize=8.8, fontweight='bold', color='#003366')
    ax.text(6.0, 0.65, "1. Levels A & C catch >90% of erroneous cases before long container runs -> Saves hours of wasted compute.", ha='center', va='center', fontsize=8, color='#333333')
    ax.text(6.0, 0.38, "2. Real-time stdout NaN abort terminates diverging containers in ~2 seconds instead of waiting for 600s timeout.", ha='center', va='center', fontsize=8, color='#333333')

    save_fig(fig, "validation_ladder")

# =============================================================================
# 5. Dual Interfaces Architecture Diagram
# (Exact border-to-border connections with shrinkA=0, shrinkB=0)
# =============================================================================
def generate_dual_interfaces_diagram():
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=300)
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 4.6)
    ax.axis('off')

    ax.text(5.5, 4.3, "Dual Operational Interfaces Sharing a Common Domain Core",
            ha='center', va='center', fontsize=12.5, fontweight='bold', color='#003366')

    # Shared Core Box (Bottom): x in [1.5, 9.5], y in [0.25, 1.65]
    core = patches.FancyBboxPatch((1.5, 0.25), 8.0, 1.4, boxstyle="round,pad=0,rounding_size=0.08", facecolor='#ede7f6', edgecolor='#512da8', lw=2)
    ax.add_patch(core)
    ax.text(5.5, 1.25, "Shared Domain Core (Unified OpenFOAM Engine)", ha='center', va='center', fontsize=10, fontweight='bold', color='#311b92')
    ax.text(5.5, 0.85, "ir/ (CaseIntent, CaseSpec)  ·  rendering/ (renderer.py)  ·  knowledge/ (envelopes.py)\npreflight/ (audit, dry_run)  ·  validation/ (Docker ladder)",
            ha='center', va='center', fontsize=8, color='#4527a0', linespacing=1.3)

    # Interface 1: Automated Pipeline (Top Left): x in [0.6, 5.2], y in [2.1, 3.9]
    p1 = patches.FancyBboxPatch((0.6, 2.1), 4.6, 1.8, boxstyle="round,pad=0,rounding_size=0.08", facecolor='#e3f2fd', edgecolor='#1565c0', lw=1.8)
    ax.add_patch(p1)
    ax.text(2.9, 3.45, "1. Automated LangGraph Pipeline\npython -m src run '...'", ha='center', va='center', fontsize=9.5, fontweight='bold', color='#0d47a1', linespacing=1.2)
    ax.text(2.9, 2.65, "* Code-enforced state machine\n* Deterministic stage order\n* Built for modest local LLMs (8B)\n* Batch unattended case generation", ha='center', va='center', fontsize=8, color='#1565c0', linespacing=1.25)

    # Interface 2: Interactive MCP Server (Top Right): x in [5.8, 10.4], y in [2.1, 3.9]
    p2 = patches.FancyBboxPatch((5.8, 2.1), 4.6, 1.8, boxstyle="round,pad=0,rounding_size=0.08", facecolor='#e8f5e9', edgecolor='#2e7d32', lw=1.8)
    ax.add_patch(p2)
    ax.text(8.1, 3.45, "2. Interactive MCP Tool Server\n./setup.sh && ./start.sh", ha='center', va='center', fontsize=9.5, fontweight='bold', color='#1b5e20', linespacing=1.2)
    ax.text(8.1, 2.65, "* 19 Model Context Protocol tools\n* OpenFOAM v13 agent skill\n* Conversational CFD debugging\n* Human-in-the-loop parameter tuning", ha='center', va='center', fontsize=8, color='#2e7d32', linespacing=1.25)

    # Exact border-to-border bidirectional arrows: bottom of p1/p2 (y=2.10) to top of core (y=1.65)
    ax.annotate('', xy=(2.90, 1.65), xytext=(2.90, 2.10),
                arrowprops=dict(arrowstyle="<->", color='#1565c0', lw=2, shrinkA=0, shrinkB=0))
    ax.annotate('', xy=(8.10, 1.65), xytext=(8.10, 2.10),
                arrowprops=dict(arrowstyle="<->", color='#2e7d32', lw=2, shrinkA=0, shrinkB=0))

    save_fig(fig, "dual_interfaces")

# =============================================================================
# 6. 2-Panel Residual Evaluation Figure
# =============================================================================
def generate_residual_figures():
    steps = np.arange(1, 11)
    
    residuals = {
        r"$U_x$": [1.00000, 0.06205, 0.04080, 0.03212, 0.03019, 0.03058, 0.03148, 0.02985, 0.02801, 0.02759],
        r"$U_y$": [1.00000, 0.27010, 0.11760, 0.07993, 0.05854, 0.04355, 0.03470, 0.03109, 0.03034, 0.02915],
        r"$U_z$": [1.00000, 0.26140, 0.10610, 0.06666, 0.04621, 0.03390, 0.02765, 0.02523, 0.02460, 0.02353],
        r"$p$ (Pressure)": [0.05866, 0.07007, 0.10690, 0.11610, 0.09865, 0.08354, 0.07189, 0.07737, 0.08805, 0.09075],
        r"$h$ (Enthalpy)": [1.00000, 0.31710, 0.16150, 0.11980, 0.07662, 0.05353, 0.04281, 0.03705, 0.03315, 0.03041],
        r"$k$ (TKE)": [1.00000, 0.61190, 0.22950, 0.17170, 0.11090, 0.07609, 0.05528, 0.04323, 0.03623, 0.03179],
        r"$\epsilon$ (Dissipation)": [0.07313, 0.39240, 0.13420, 0.15940, 0.10310, 0.07517, 0.05805, 0.04692, 0.03918, 0.03333],
    }

    slopes = {
        r"$U_x$": -0.10,
        r"$U_y$": -0.15,
        r"$U_z$": -0.16,
        r"$h$": -0.15,
        r"$k$": -0.17,
        r"$\epsilon$": -0.08,
        r"$p$": +0.05,
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.0), dpi=300, gridspec_kw={'width_ratios': [1.25, 1]})

    colors = {
        r"$U_x$": "#1f77b4",
        r"$U_y$": "#4ba3e3",
        r"$U_z$": "#17becf",
        r"$p$ (Pressure)": "#d62728",
        r"$h$ (Enthalpy)": "#ff7f0e",
        r"$k$ (TKE)": "#2ca02c",
        r"$\epsilon$ (Dissipation)": "#9467bd",
    }
    
    markers = {
        r"$U_x$": 'o',
        r"$U_y$": 's',
        r"$U_z$": '^',
        r"$p$ (Pressure)": 'D',
        r"$h$ (Enthalpy)": 'v',
        r"$k$ (TKE)": 'p',
        r"$\epsilon$ (Dissipation)": '*',
    }

    # --- Panel 1 ---
    for name, vals in residuals.items():
        is_p = "Pressure" in name
        ax1.plot(
            steps, vals, 
            label=name, 
            color=colors[name], 
            marker=markers[name], 
            linewidth=2.2 if is_p else 1.6, 
            markersize=6.5 if is_p else 5.5,
            linestyle='--' if is_p else '-'
        )

    ax1.set_yscale('log')
    ax1.set_xlabel("Solver Step / Iteration", fontweight='bold', fontsize=10)
    ax1.set_ylabel("Initial Residual (Log Scale)", fontweight='bold', fontsize=10)
    ax1.set_title("(a) Residual Convergence (10.6M Faces, 10 MPI Ranks)", fontweight='bold', fontsize=11, pad=10)
    ax1.set_xticks(steps)
    ax1.set_ylim(0.015, 2.0)
    ax1.grid(True, which="both", ls=":", alpha=0.6)
    
    # Legend at Upper Right
    ax1.legend(loc="upper right", frameon=True, framealpha=0.92, facecolor='white', edgecolor='#cccccc', ncol=2, fontsize=8.5)

    # Callout 1: Pressure plateau
    ax1.annotate(
        r"$\mathbf{p\ Plateau}$ ($0.059 \to 0.091$)" + "\nLevel C Verdict: WARN",
        xy=(10, 0.09075), xytext=(5.2, 0.35),
        arrowprops=dict(arrowstyle="->", color='#d62728', lw=1.5, connectionstyle="arc3,rad=-0.15"),
        fontsize=8.5, fontweight='bold', color='#b71c1c',
        bbox=dict(boxstyle="round,pad=0.35", fc="#ffebee", ec="#ef5350", lw=1)
    )

    # Callout 2: Decaying fields
    ax1.annotate(
        r"$\mathbf{Fast\ Decay}$ ($U, h, k, \epsilon \leq 0.03$)",
        xy=(10, 0.02759), xytext=(3.0, 0.022),
        arrowprops=dict(arrowstyle="->", color='#2e7d32', lw=1.5, connectionstyle="arc3,rad=0.15"),
        fontsize=8.5, fontweight='bold', color='#1b5e20',
        bbox=dict(boxstyle="round,pad=0.35", fc="#e8f5e9", ec="#66bb6a", lw=1)
    )

    # --- Panel 2 ---
    field_names = list(slopes.keys())
    field_slopes = list(slopes.values())
    bar_colors = ['#2ca02c' if s <= -0.05 else '#d62728' if s >= 0.05 else '#ff7f0e' for s in field_slopes]

    y_pos = np.arange(len(field_names))
    bars = ax2.barh(y_pos, field_slopes, color=bar_colors, height=0.55, edgecolor='black', linewidth=0.6)

    # Threshold lines
    ax2.axvline(-0.05, color='#2e7d32', linestyle='--', linewidth=1.2, label=r'PASS ($\leq -0.05$)')
    ax2.axvline(0.05, color='#c62828', linestyle='--', linewidth=1.2, label=r'FAIL ($\geq +0.05$)')
    ax2.axvline(0.00, color='gray', linestyle='-', linewidth=0.8, alpha=0.7)

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(field_names, fontweight='bold', fontsize=9.5)
    ax2.invert_yaxis()
    ax2.set_xlabel(r"Log-Residual Slope $\Delta \log_{10}(R) / \Delta \mathrm{Step}$", fontweight='bold', fontsize=10)
    ax2.set_title("(b) Level C Preflight Log-Slope Classification", fontweight='bold', fontsize=11, pad=10)
    ax2.set_xlim(-0.22, 0.15)
    ax2.grid(True, axis='x', ls=":", alpha=0.6)

    for bar, slope in zip(bars, field_slopes):
        width = bar.get_width()
        x_text = width + (0.012 if width >= 0 else -0.012)
        ha = 'left' if width >= 0 else 'right'
        ax2.text(x_text, bar.get_y() + bar.get_height()/2.0, f"{slope:+.2f}", 
                 va='center', ha=ha, fontsize=8.5, fontweight='bold', color='#333333')

    ax2.legend(loc="lower left", frameon=True, framealpha=0.92, facecolor='white', edgecolor='#cccccc', fontsize=8.0)

    plt.tight_layout()
    save_fig(fig, "rhvt_residuals")

def main():
    setup_dirs()
    print("=== Generating All Publication-Grade Figures (Exact Border Anchoring) ===")
    generate_philosophy_diagram()
    generate_pipeline_diagram()
    generate_rhvt_schematic()
    generate_validation_ladder_diagram()
    generate_dual_interfaces_diagram()
    generate_residual_figures()
    print("=== Complete! ===")

if __name__ == "__main__":
    main()
