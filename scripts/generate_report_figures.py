#!/usr/bin/env python3
"""
Generate publication-quality 2-panel residual convergence and log-slope analysis figures
for the RHVT reference case.
Data sourced from docs/evidence/rhvt-residuals-2026-08-18.md
"""

import os
import matplotlib.pyplot as plt
import numpy as np

def main():
    steps = np.arange(1, 11)
    
    # Residual data from _w10_rhvt on 10 MPI ranks
    residuals = {
        r"$U_x$": [1.00000, 0.06205, 0.04080, 0.03212, 0.03019, 0.03058, 0.03148, 0.02985, 0.02801, 0.02759],
        r"$U_y$": [1.00000, 0.27010, 0.11760, 0.07993, 0.05854, 0.04355, 0.03470, 0.03109, 0.03034, 0.02915],
        r"$U_z$": [1.00000, 0.26140, 0.10610, 0.06666, 0.04621, 0.03390, 0.02765, 0.02523, 0.02460, 0.02353],
        r"$p$ (Pressure)": [0.05866, 0.07007, 0.10690, 0.11610, 0.09865, 0.08354, 0.07189, 0.07737, 0.08805, 0.09075],
        r"$h$ (Enthalpy)": [1.00000, 0.31710, 0.16150, 0.11980, 0.07662, 0.05353, 0.04281, 0.03705, 0.03315, 0.03041],
        r"$k$ (TKE)": [1.00000, 0.61190, 0.22950, 0.17170, 0.11090, 0.07609, 0.05528, 0.04323, 0.03623, 0.03179],
        r"$\epsilon$ (Dissipation)": [0.07313, 0.39240, 0.13420, 0.15940, 0.10310, 0.07517, 0.05805, 0.04692, 0.03918, 0.03333],
    }

    # Log-slopes per field
    slopes = {
        r"$U_x$": -0.10,
        r"$U_y$": -0.15,
        r"$U_z$": -0.16,
        r"$h$": -0.15,
        r"$k$": -0.17,
        r"$\epsilon$": -0.08,
        r"$p$": +0.05,
    }

    # Set overall aesthetic
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.size': 10,
        'axes.titlesize': 11,
        'axes.labelsize': 10.5,
        'xtick.labelsize': 9.5,
        'ytick.labelsize': 9.5,
        'legend.fontsize': 9,
        'figure.titlesize': 13
    })

    # Create 2-panel figure
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.8), dpi=300, gridspec_kw={'width_ratios': [1.35, 1]})

    # Colors
    colors = {
        r"$U_x$": "#1f77b4",
        r"$U_y$": "#4ba3e3",
        r"$U_z$": "#17becf",
        r"$p$ (Pressure)": "#d62728", # Red
        r"$h$ (Enthalpy)": "#ff7f0e", # Orange
        r"$k$ (TKE)": "#2ca02c",     # Green
        r"$\epsilon$ (Dissipation)": "#9467bd", # Purple
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

    # --- Panel 1: Residual Convergence ---
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
    ax1.set_xlabel("Solver Step / Iteration", fontweight='bold')
    ax1.set_ylabel("Initial Residual (Log Scale)", fontweight='bold')
    ax1.set_title("(a) Residual Convergence (10.6M Faces, 10 MPI Ranks)", fontweight='bold', pad=8)
    ax1.set_xticks(steps)
    ax1.set_ylim(0.015, 1.8)
    ax1.grid(True, which="both", ls=":", alpha=0.6)
    
    # Non-overlapping Legend at lower left
    ax1.legend(loc="lower left", frameon=True, framealpha=0.92, facecolor='white', edgecolor='#cccccc', ncol=2)

    # Clean Callout 1: Pressure plateau (placed in middle right where space is completely clear)
    ax1.annotate(
        r"$\mathbf{p\ Plateau}$ ($0.059 \to 0.091$)" + "\nLevel C Verdict: WARN",
        xy=(10, 0.09075), xytext=(6.2, 0.45),
        arrowprops=dict(arrowstyle="->", color='#d62728', lw=1.5, connectionstyle="arc3,rad=-0.15"),
        fontsize=9, fontweight='bold', color='#b71c1c',
        bbox=dict(boxstyle="round,pad=0.35", fc="#ffebee", ec="#ef5350", lw=1)
    )

    # Clean Callout 2: Decaying fields (placed in upper right where space is open)
    ax1.annotate(
        r"$\mathbf{Fast\ Decay}$ ($U, h, k, \epsilon \leq 0.03$)",
        xy=(10, 0.02759), xytext=(4.2, 0.022),
        arrowprops=dict(arrowstyle="->", color='#2e7d32', lw=1.5, connectionstyle="arc3,rad=0.15"),
        fontsize=9, fontweight='bold', color='#1b5e20',
        bbox=dict(boxstyle="round,pad=0.35", fc="#e8f5e9", ec="#66bb6a", lw=1)
    )

    # --- Panel 2: Log-Slope Decay Rates vs Preflight Gates ---
    field_names = list(slopes.keys())
    field_slopes = list(slopes.values())
    bar_colors = ['#2ca02c' if s <= -0.05 else '#d62728' if s >= 0.05 else '#ff7f0e' for s in field_slopes]

    y_pos = np.arange(len(field_names))
    bars = ax2.barh(y_pos, field_slopes, color=bar_colors, height=0.55, edgecolor='black', linewidth=0.6)

    # Threshold lines
    ax2.axvline(-0.05, color='#2e7d32', linestyle='--', linewidth=1.2, label=r'PASS Threshold ($\leq -0.05$)')
    ax2.axvline(0.05, color='#c62828', linestyle='--', linewidth=1.2, label=r'FAIL Threshold ($\geq +0.05$)')
    ax2.axvline(0.00, color='gray', linestyle='-', linewidth=0.8, alpha=0.7)

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(field_names, fontweight='bold')
    ax2.invert_yaxis()  # Top-down order
    ax2.set_xlabel(r"Log-Residual Slope $\Delta \log_{10}(R) / \Delta \mathrm{Step}$", fontweight='bold')
    ax2.set_title("(b) Level C Preflight Log-Slope Classification", fontweight='bold', pad=8)
    ax2.set_xlim(-0.22, 0.12)
    ax2.grid(True, axis='x', ls=":", alpha=0.6)

    # Annotate slope values next to bars
    for bar, slope in zip(bars, field_slopes):
        width = bar.get_width()
        x_text = width + (0.012 if width >= 0 else -0.012)
        ha = 'left' if width >= 0 else 'right'
        ax2.text(x_text, bar.get_y() + bar.get_height()/2.0, f"{slope:+.2f}", 
                 va='center', ha=ha, fontsize=8.5, fontweight='bold', color='#333333')

    ax2.legend(loc="lower right", frameon=True, framealpha=0.92, facecolor='white', edgecolor='#cccccc', fontsize=8.5)

    plt.tight_layout()

    # Save to temp/report/figures and docs/evidence
    out_dirs = [
        "temp/report/figures",
        "docs/evidence"
    ]
    for d in out_dirs:
        os.makedirs(d, exist_ok=True)
        png_path = os.path.join(d, "rhvt_residuals.png")
        pdf_path = os.path.join(d, "rhvt_residuals.pdf")
        fig.savefig(png_path, dpi=300, bbox_inches='tight')
        fig.savefig(pdf_path, bbox_inches='tight')
        print(f"Saved: {png_path} and {pdf_path}")

if __name__ == "__main__":
    main()
