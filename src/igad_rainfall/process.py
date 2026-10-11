"""Turn daily CHIRPS rasters into analysis-ready products: accumulations, alert levels, COGs and Zarr."""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import rioxarray
import xarray as xr

from .chirps import NODATA

log = logging.getLogger(__name__)


def load_stack(paths: list[Path], dates: list[dt.date], chunks: dict | None = None) -> xr.DataArray:
    """Stack daily GeoTIFFs into a lazy (time, y, x) DataArray of rainfall in mm."""
    if len(paths) != len(dates):
        raise ValueError("paths and dates must have the same length")
    layers = [
        rioxarray.open_rasterio(p, masked=True, chunks=chunks or {"x": 256, "y": 256}).squeeze("band", drop=True)
        for p in paths
    ]
    da = xr.concat(layers, dim=pd.Index(pd.to_datetime(dates), name="time"))
    da.name = "precip"
    # Replace (not update) attrs: GeoTIFF scale_factor/add_offset would clash with Zarr encoding.
    da.attrs = {"units": "mm/day", "long_name": "CHIRPS v2.0 daily precipitation", "source": "UCSB CHC"}
    return da.sortby("time")


def accumulate(da: xr.DataArray, days: int) -> xr.DataArray:
    """Total rainfall over the last `days` days of the stack. Pixels with any missing day stay NaN."""
    if days > da.sizes["time"]:
        raise ValueError(f"Need {days} days of data, have {da.sizes['time']}")
    window = da.isel(time=slice(-days, None))
    acc = window.sum("time", skipna=False)
    acc.name = f"precip_{days}d"
    acc.attrs.update(units="mm", long_name=f"{days}-day accumulated precipitation",
                     end_date=str(pd.Timestamp(da.time.values[-1]).date()))
    return acc.rio.write_crs(da.rio.crs)


def alert_level(acc: xr.DataArray, thresholds: tuple[float, ...]) -> xr.DataArray:
    """Classify accumulated rainfall into alert levels 0 (none) .. len(thresholds) (highest)."""
    if list(thresholds) != sorted(thresholds):
        raise ValueError("thresholds must be ascending")
    levels = xr.zeros_like(acc, dtype="uint8")
    for i, t in enumerate(thresholds, start=1):
        levels = xr.where(acc >= t, np.uint8(i), levels)
    levels = levels.where(acc.notnull(), np.uint8(255)).astype("uint8")
    levels.name = "alert_level"
    levels.attrs = {"long_name": "Heavy rainfall alert level", "thresholds_mm": list(thresholds),
                    "_FillValue": 255}
    return levels.rio.write_crs(acc.rio.crs).rio.write_nodata(255)


def write_cog(da: xr.DataArray, path: Path) -> Path:
    """Write a 2-D DataArray as a Cloud-Optimised GeoTIFF with overviews."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = da.compute()
    if np.issubdtype(out.dtype, np.floating):
        out = out.fillna(NODATA).rio.write_nodata(NODATA)
    out.rio.to_raster(path, driver="COG", compress="DEFLATE", overview_resampling="average")
    log.info("Wrote COG %s", path)
    return path


def update_zarr(da: xr.DataArray, path: Path) -> Path:
    """Append days not yet in the Zarr store (idempotent: re-running a day does not duplicate it)."""
    ds = da.to_dataset().drop_vars("spatial_ref", errors="ignore")
    if path.exists():
        existing = xr.open_zarr(path)
        latest = existing.time.max().values
        ds = ds.sel(time=ds.time > latest)
        if ds.sizes["time"] == 0:
            log.info("Zarr %s already up to date", path)
            return path
        ds.chunk({"time": 1, "y": 256, "x": 256}).to_zarr(path, append_dim="time")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        ds.chunk({"time": 1, "y": 256, "x": 256}).to_zarr(path, mode="w")
    log.info("Zarr %s updated with %d day(s)", path, ds.sizes["time"])
    return path
