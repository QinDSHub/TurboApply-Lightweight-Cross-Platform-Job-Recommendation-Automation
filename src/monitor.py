from pathlib import Path
import pandas as pd
import numpy as np
import logging
import argparse
from utils import load_config


logger = logging.getLogger(__name__)


def main(metric_data_path: Path, month_report_path: Path)->None:
    """Run the monthly report generation and save the report as CSV file.
    
    Args:
        metric_data_path (Path): the collected data from daily application records.

    Returns:
        None: This function writes the report CSV to disk and does not return a value.
    """
    
    df = pd.read_csv(metric_data_path, dtype={"apply_date":str})

    df['apply_date'] = pd.to_datetime(df['apply_date'], format='%Y%m%d')
    df['applied'] = df['applied'].fillna(0).astype(int)
    df['review']  = df['review'].fillna(0).astype(int)
    df['month']   = df['apply_date'].dt.to_period('M')

    tmin = pd.to_datetime(df['time_min'], format='%H:%M')
    tmax = pd.to_datetime(df['time_max'], format='%H:%M')
    df['time_diff'] = ((tmax-tmin).dt.total_seconds())/60
    df['duration_per_application'] = round(df['time_diff']/df['applied'],2)
    df['duration_per_review'] = round(df['time_diff']/df['review'],2)

    df['detail_min'] = (df['applied'] * df['duration_per_application']
                        + df['review'] * df['duration_per_review'])
    df['diff_gap'] = (df['time_diff'] - df['detail_min']).abs()

    rows = []
    for m, g in df.groupby('month'):
        days_in_month = m.days_in_month
        active_days = g.loc[g['applied'] > 0, 'apply_date'].nunique()

        total_applied = g['applied'].sum()
        total_review  = g['review'].sum()
        total_min     = g['time_diff'].sum()

        app_min = (g['applied'] * g['duration_per_application']).sum()
        rev_min = (g['review']  * g['duration_per_review']).sum()
        per_app = app_min / total_applied if total_applied else np.nan
        per_rev = rev_min / total_review  if total_review  else np.nan

        rows.append({
            'month': str(m),
            'days_in_month': days_in_month,
            'active_days': active_days,
            'total_applied': total_applied,
            'total_review': total_review,
            'avg_applied_active': total_applied / active_days if active_days else np.nan,
            'avg_review_active':  total_review  / active_days if active_days else np.nan,
            'avg_applied_all': total_applied / days_in_month,
            'avg_review_all':  total_review  / days_in_month,
            'ratio_app_rev': f"1 : {total_review/total_applied:.2f}" if total_applied else "",
            'per_applied_min': per_app,
            'per_review_min': per_rev,
            'total_hours': total_min / 60
        })
    report = pd.DataFrame(rows).sort_values('month')
    report.to_csv(month_report_path, index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    arg_parser = argparse.ArgumentParser(description="generate monthly application report for online metric monitoring")
    arg_parser.add_argument("--config", type=str, default=str(PROJECT_ROOT / "config.yaml"))
    args = arg_parser.parse_args()

    cfg = load_config(args.config)
    config_dir = Path(args.config).resolve().parent

    def resolve_path(base: Path, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (base / p).resolve()

    metric_data_path = resolve_path(config_dir, Path(cfg["metric_data_path"]))
    month_report_path = resolve_path(config_dir, Path(cfg["month_report_path"]))

    main(metric_data_path,month_report_path)

    logger.info("🚀 Monthly report has been generated and saved!")
