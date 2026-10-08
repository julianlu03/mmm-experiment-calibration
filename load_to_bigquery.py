"""
Load the simulated exports into BigQuery, the way an ELT tool (Fivetran,
Airbyte) would land them: one raw table per source, column names normalized
to snake_case, values otherwise untouched (dates stay strings, signs and
units stay as exported). All cleaning happens downstream in dbt.

Datasets
  <project>.mmm_raw     the six raw exports       -> dbt sources
  <project>.mmm_truth   ground truth (evaluation only, never a dbt source for modeling)

Auth (once):  gcloud auth application-default login
Usage:        python load_to_bigquery.py --project YOUR_GCP_PROJECT
              python load_to_bigquery.py --project YOUR_GCP_PROJECT --dry-run
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RAW_FILES = [
    "google_ads_geo_daily",
    "meta_ads_region_daily",
    "shopify_sales_daily",
    "promo_calendar",
    "geo_experiment_design",
    "census_state_population",
]
ID_COLUMNS = {"campaign_id", "Campaign ID"}  # keep IDs as strings, like connectors do


def snake(col):
    return re.sub(r"[^0-9a-zA-Z]+", "_", col).strip("_").lower()


def read_raw(path):
    df = pd.read_csv(path, dtype={c: str for c in ID_COLUMNS})
    df.columns = [snake(c) for c in df.columns]
    df["_loaded_at"] = datetime.now(timezone.utc)
    return df


def truth_frames(truth_dir):
    frames = {"truth_geo_week": pd.read_csv(truth_dir / "truth_geo_week.csv")}
    with open(truth_dir / "truth_summary.json") as f:
        s = json.load(f)
    frames["truth_channel_roi"] = pd.DataFrame([{"channel": c, **v} for c, v in s["channels"].items()])
    exp = {k: v for k, v in s["experiment"].items() if k != "treated_geos"}
    exp["treated_geos"] = ",".join(s["experiment"]["treated_geos"])
    frames["truth_experiment"] = pd.DataFrame([exp])
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="US")
    ap.add_argument("--raw-dataset", default="mmm_raw")
    ap.add_argument("--truth-dataset", default="mmm_truth")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = Path(args.data_dir)
    jobs = [(args.raw_dataset, name, read_raw(data / "raw" / f"{name}.csv")) for name in RAW_FILES]
    jobs += [(args.truth_dataset, name, df) for name, df in truth_frames(data / "truth").items()]

    if args.dry_run:
        for ds, name, df in jobs:
            print(f"{args.project}.{ds}.{name}: {len(df):,} rows")
            print("   " + ", ".join(f"{c} ({t})" for c, t in df.dtypes.astype(str).items()))
        return

    from google.cloud import bigquery

    client = bigquery.Client(project=args.project, location=args.location)
    for ds in {args.raw_dataset, args.truth_dataset}:
        dataset = bigquery.Dataset(f"{args.project}.{ds}")
        dataset.location = args.location
        client.create_dataset(dataset, exists_ok=True)

    cfg = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    for ds, name, df in jobs:
        table_id = f"{args.project}.{ds}.{name}"
        client.load_table_from_dataframe(df, table_id, job_config=cfg).result()
        print(f"Loaded {table_id}: {client.get_table(table_id).num_rows:,} rows")


if __name__ == "__main__":
    main()
