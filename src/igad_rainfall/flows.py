"""Prefect flow for the daily rainfall monitoring cycle.

    ingest CHIRPS -> accumulations + alert raster -> COG + Zarr -> STAC -> district stats -> PostGIS

Run once:       python -m igad_rainfall.flows --date 2026-06-30
Run on a cron:  prefect deploy --all   (see prefect.yaml)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from prefect import flow, get_run_logger, task

from . import chirps, process, stac, zonal
from .config import GHA_BBOX, Settings


@task(retries=3, retry_delay_seconds=60)
def fetch(day: dt.date, settings: Settings) -> Path:
    return chirps.fetch_day(day, settings)


@task
def build_products(paths: list[Path], days: list[dt.date], settings: Settings) -> dict[str, Path]:
    da = process.load_stack(paths, days)
    end = days[-1]
    outputs: dict[str, Path] = {}
    accumulations = {}
    for w in settings.windows:
        acc = process.accumulate(da, w)
        accumulations[w] = acc
        outputs[f"precip_{w}d"] = process.write_cog(acc, settings.cog_dir / f"precip_{w}d_{end:%Y%m%d}.tif")
    alerts = process.alert_level(accumulations[3], settings.alert_thresholds)
    outputs["alert_level"] = process.write_cog(alerts, settings.cog_dir / f"alert_level_{end:%Y%m%d}.tif")
    process.update_zarr(da, settings.zarr_path)
    return outputs


@task
def publish_stac(day: dt.date, assets: dict[str, Path], settings: Settings, validate: bool) -> Path:
    item = stac.build_item(day, assets, settings.alert_thresholds, settings.public_base_url)
    return stac.update_catalog(settings.stac_dir, item, list(GHA_BBOX), validate=validate)


@task
def district_stats(day: dt.date, assets: dict[str, Path], settings: Settings):
    import rioxarray

    gdf = zonal.load_boundaries(settings)
    layers = {k: rioxarray.open_rasterio(p, masked=True).squeeze("band", drop=True)
              for k, p in assets.items() if k.startswith("precip_")}
    stats = zonal.zonal_stats(gdf, layers)
    stats["alert_level"] = zonal.classify(stats["precip_3d_p90"], settings.alert_thresholds)
    out = settings.data_dir / "stats" / f"district_stats_{day:%Y%m%d}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    stats.to_csv(out, index=False)
    return gdf, stats


@task(retries=2, retry_delay_seconds=30)
def load_postgis(day: dt.date, gdf, stats, settings: Settings) -> int:
    from . import db

    db.init_schema(settings.database_url)
    db.load_boundaries(settings.database_url, gdf)
    return db.load_day(settings.database_url, day, stats)


@flow(name="gha-daily-rainfall", log_prints=True)
def daily_rainfall(date: str | None = None, validate_stac: bool = True) -> dict:
    """Process the given day (YYYY-MM-DD), or the latest day CHIRPS has published."""
    log = get_run_logger()
    settings = Settings()
    end = dt.date.fromisoformat(date) if date else chirps.latest_available_date()
    days = [end - dt.timedelta(days=i) for i in range(max(settings.windows) - 1, -1, -1)]
    log.info("Processing %s (%d-day window)", end, len(days))

    paths = [f.result() for f in fetch.map(days, settings=settings)]
    assets = build_products(paths, days, settings)
    collection = publish_stac(end, assets, settings, validate_stac)
    gdf, stats = district_stats(end, assets, settings)
    loaded = load_postgis(end, gdf, stats, settings) if settings.database_url else 0

    summary = {
        "date": str(end),
        "assets": {k: str(v) for k, v in assets.items()},
        "stac": str(collection),
        "districts": len(stats),
        "districts_on_alert": int((stats["alert_level"].fillna(0) > 0).sum()),
        "postgis_rows": loaded,
    }
    log.info(json.dumps(summary))
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="End date YYYY-MM-DD (default: latest available)")
    parser.add_argument("--no-validate", action="store_true", help="Skip STAC schema validation (offline)")
    args = parser.parse_args()
    daily_rainfall(date=args.date, validate_stac=not args.no_validate)


if __name__ == "__main__":
    main()
