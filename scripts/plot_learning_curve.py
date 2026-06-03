"""Visualise model learning curve from ModelRegistry metadata.

Usage:
    python scripts/plot_learning_curve.py --league EPL
    python scripts/plot_learning_curve.py --league EPL --output charts/epl_curve.png
"""

from __future__ import annotations

import argparse
import json
import sys
from glob import glob
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot model learning curve over time.")
    parser.add_argument("--league", default="EPL")
    parser.add_argument("--model-dir", default="data/models", type=Path)
    parser.add_argument("--output", default="learning_curve.png")
    parser.add_argument(
        "--baseline-brier",
        type=float,
        default=0.25,
        help="Brier score of naive random baseline (default: 0.25)",
    )
    parser.add_argument(
        "--baseline-logloss",
        type=float,
        default=0.693,
        help="Log-loss of naive random baseline ln(2) (default: 0.693)",
    )
    args = parser.parse_args()

    meta_files = sorted(glob(str(args.model_dir / f"dc_{args.league}_*.meta.json")))
    if not meta_files:
        print(f"No model metadata found for {args.league} in {args.model_dir}", file=sys.stderr)
        print("Run the daily trainer at least once to generate model files.", file=sys.stderr)
        sys.exit(1)

    models = []
    for path in meta_files:
        try:
            meta = json.loads(Path(path).read_text(encoding="utf-8"))
            brier = meta.get("brier_score")
            log_loss = meta.get("log_loss")
            n_matches = meta.get("n_matches")
            created = meta.get("created_at_utc", "")
            if brier is None or log_loss is None:
                continue
            models.append(
                {
                    "timestamp": created,
                    "model_id": meta.get("model_id", ""),
                    "brier": float(brier),
                    "log_loss": float(log_loss),
                    "n_matches": int(n_matches or 0),
                    "status": meta.get("status", "candidate"),
                    "promoted": meta.get("status") == "production",
                }
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    if not models:
        print("No valid model records found (missing brier_score/log_loss).", file=sys.stderr)
        sys.exit(1)

    models.sort(key=lambda m: m["timestamp"])

    print(f"Found {len(models)} model version(s) for {args.league}:")
    print(f"{'Model ID':<40} {'Brier':>8} {'LogLoss':>8} {'Matches':>8} {'Status'}")
    print("-" * 80)
    for m in models:
        star = "★" if m["promoted"] else " "
        print(
            f"{star} {m['model_id']:<38} {m['brier']:>8.4f} {m['log_loss']:>8.4f} "
            f"{m['n_matches']:>8} {m['status']}"
        )

    first_brier = models[0]["brier"]
    last_brier = models[-1]["brier"]
    improvement = first_brier - last_brier
    print(
        f"\nBrier improvement: {first_brier:.4f} → {last_brier:.4f} "
        f"({'↓' if improvement > 0 else '↑'}{abs(improvement):.4f})"
    )

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd

        df = pd.DataFrame(models)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        fig.suptitle(f"Model Learning Curve — {args.league}", fontsize=14)

        ax1 = axes[0]
        ax1.plot(df["timestamp"], df["brier"], "o-", color="steelblue", linewidth=2)
        promoted = df[df["promoted"]]
        if not promoted.empty:
            ax1.scatter(
                promoted["timestamp"],
                promoted["brier"],
                color="gold",
                s=100,
                zorder=5,
                label="promoted",
            )
        ax1.axhline(
            y=args.baseline_brier,
            color="red",
            linestyle="--",
            alpha=0.5,
            label=f"baseline ({args.baseline_brier:.3f})",
        )
        ax1.set_title("Brier Score (lower = better)")
        ax1.set_ylabel("Brier Score")
        ax1.legend()
        ax1.tick_params(axis="x", rotation=30)

        ax2 = axes[1]
        ax2.plot(df["timestamp"], df["log_loss"], "o-", color="coral", linewidth=2)
        ax2.axhline(
            y=args.baseline_logloss,
            color="red",
            linestyle="--",
            alpha=0.5,
            label=f"baseline ({args.baseline_logloss:.3f})",
        )
        ax2.set_title("Log Loss (lower = better)")
        ax2.set_ylabel("Log Loss")
        ax2.legend()
        ax2.tick_params(axis="x", rotation=30)

        ax3 = axes[2]
        ax3.plot(df["timestamp"], df["n_matches"], "o-", color="seagreen", linewidth=2)
        ax3.set_title("Training matches (growing = learning)")
        ax3.set_ylabel("Matches")
        ax3.tick_params(axis="x", rotation=30)

        plt.tight_layout()
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        print(f"\nSaved chart → {out_path}")

    except ImportError:
        print("\nmatplotlib not installed — text summary only (pip install matplotlib pandas).")


if __name__ == "__main__":
    main()
