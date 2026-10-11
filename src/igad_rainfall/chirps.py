"""Ingest CHIRPS v2.0 daily rainfall for the Greater Horn of Africa.

CHIRPS publishes global daily Cloud-Optimised GeoTIFFs, so we read only the GHA window over HTTP
range requests (GDAL /vsicurl/) instead of downloading the ~8 MB global file.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.windows import from_bounds

from .config import CHIRPS_DAILY_URL, CHIRPS_YEAR_INDEX, GHA_BBOX, Settings

log = logging.getLogger(__name__)
NODATA = -9999.0
_DATE_RE = re.compile(r"chirps-v2\.0\.(\d{4})\.(\d{2})\.(\d{2})\.cog")

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".cog,.tif",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


def daily_url(day: dt.date) -> str:
    return CHIRPS_DAILY_URL.format(year=day.year, month=day.month, day=day.day)


def latest_available_date(today: dt.date | None = None, timeout: int = 30) -> dt.date:
    """Return the most recent day published on the CHIRPS server (final product lags ~4-8 weeks)."""
    today = today or dt.date.today()
    for year in (today.year, today.year - 1):
        resp = requests.get(CHIRPS_YEAR_INDEX.format(year=year), timeout=timeout)
        if resp.status_code != 200:
            continue
        dates = [dt.date(int(y), int(m), int(d)) for y, m, d in _DATE_RE.findall(resp.text)]
        dates = [d for d in dates if d <= today]
        if dates:
            return max(dates)
    raise RuntimeError("No CHIRPS daily files found for this or last year")


def fetch_day(day: dt.date, settings: Settings, bbox: tuple[float, float, float, float] = GHA_BBOX) -> Path:
    """Clip one CHIRPS day to the bbox and save it as a local GeoTIFF. Skips days already fetched."""
    out = settings.raw_dir / f"chirps_{day:%Y%m%d}.tif"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(**GDAL_ENV), rasterio.open(f"/vsicurl/{daily_url(day)}") as src:
        window = from_bounds(*bbox, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=window).astype("float32")
        profile = src.profile | {
            "driver": "GTiff",
            "height": data.shape[0],
            "width": data.shape[1],
            "transform": src.window_transform(window),
            "dtype": "float32",
            "nodata": NODATA,
            "compress": "deflate",
            "tiled": False,
        }
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
    data[data < 0] = NODATA
    tmp = out.with_suffix(".tmp")
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(data, 1)
    tmp.replace(out)  # atomic: a half-written file is never mistaken for a finished one
    log.info("Fetched CHIRPS %s -> %s (%s px, max %.1f mm)", day, out, data.shape, np.nanmax(data))
    return out
