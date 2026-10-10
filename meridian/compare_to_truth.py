"""
Compare Meridian ROI estimates against the simulator's ground truth.

Run from the project root (either venv works; needs only pandas):
    python meridian/compare_to_truth.py

Reads:
    outputs/meridian_v*_roi.csv   one file per model version (v1 now, v2 on Day 4)
    data/truth/truth_summary.json written by simulate.py
Writes:
    outputs/roi_vs_truth.csv      one row per model version x channel

This is the only modeling-side script that reads the truth. Fitting scripts
never do, so no model can "peek" at the answer.
"""

import json
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path("outputs")
TRUTH_PATH = Path("data/truth/truth_summary.json")
OUT_PATH = OUTPUT_DIR / "roi_vs_truth.csv"


def load_truth(path: Path) -> pd.DataFrame:
    with open(path) as f:
        truth = json.load(f)
    return pd.DataFrame(
        [{"channel": ch, "true_roi": v["true_roi"]} for ch, v in truth["channels"].items()]
    )


def load_estimates(output_dir: Path) -> pd.DataFrame:
    """Stack every model version's ROI summary; keep posterior rows and attach
    the prior mean, so we can see where the posterior sits between prior and truth."""
    files = sorted(output_dir.glob("meridian_v*_roi.csv"))
    if not files:
        raise FileNotFoundError(f"No meridian_v*_roi.csv files in {output_dir}/")
    roi = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

    prior = (
        roi[roi["distribution"] == "prior"][["model_version", "channel", "roi_mean"]]
        .rename(columns={"roi_mean": "prior_roi_mean"})
    )
    post = roi[roi["distribution"] == "posterior"].drop(columns="distribution")
    return post.merge(prior, on=["model_version", "channel"], how="left")


def main() -> None:
    est = load_estimates(OUTPUT_DIR)
    truth = load_truth(TRUTH_PATH)

    df = est.merge(truth, on="channel", how="left", validate="many_to_one")
    assert df["true_roi"].notna().all(), "A channel in the ROI file is missing from the truth file"

    df["error"] = df["roi_mean"] - df["true_roi"]
    df["pct_error"] = df["error"] / df["true_roi"]
    df["covers_truth"] = df["true_roi"].between(df["roi_ci_lo"], df["roi_ci_hi"])

    df = df[[
        "model_version", "channel", "true_roi", "prior_roi_mean",
        "roi_mean", "roi_ci_lo", "roi_ci_hi", "error", "pct_error", "covers_truth",
    ]].sort_values(["model_version", "channel"])

    df.to_csv(OUT_PATH, index=False)

    show = df.copy()
    show["pct_error"] = (show["pct_error"] * 100).map("{:+.0f}%".format)
    print("=== ROI vs truth (posterior mean, 90% credible interval) ===")
    print(show.round(2).to_string(index=False))
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
