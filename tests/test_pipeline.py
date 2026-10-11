"""Offline tests on synthetic rasters (no network, no database)."""
import datetime as dt

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from shapely.geometry import box

from igad_rainfall import process, stac, zonal
from igad_rainfall.chirps import NODATA, daily_url

DAYS = [dt.date(2026, 6, 1) + dt.timedelta(days=i) for i in range(4)]


@pytest.fixture
def daily_tifs(tmp_path):
    """Four 10x10 daily rasters at 0.05 deg: day i has rainfall 10*(i+1) mm, one nodata pixel."""
    transform = from_origin(35.0, 1.0, 0.05, 0.05)
    paths = []
    for i, _ in enumerate(DAYS):
        data = np.full((10, 10), 10.0 * (i + 1), dtype="float32")
        if i == 2:
            data[0, 0] = NODATA
        path = tmp_path / f"day{i}.tif"
        with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
                           crs="EPSG:4326", transform=transform, nodata=NODATA) as dst:
            dst.write(data, 1)
        paths.append(path)
    return paths


def test_daily_url():
    assert daily_url(dt.date(2026, 6, 3)).endswith("/2026/chirps-v2.0.2026.06.03.cog")


def test_accumulate_and_missing_days(daily_tifs):
    da = process.load_stack(daily_tifs, DAYS)
    acc3 = process.accumulate(da, 3).compute()
    assert float(acc3[5, 5]) == pytest.approx(20 + 30 + 40)
    assert np.isnan(float(acc3[0, 0]))  # a missing day must not be silently treated as 0 mm
    with pytest.raises(ValueError):
        process.accumulate(da, 5)


def test_alert_levels(daily_tifs):
    acc = process.accumulate(process.load_stack(daily_tifs, DAYS), 3)
    levels = process.alert_level(acc, (50, 90, 150)).compute()
    assert int(levels[5, 5]) == 2  # 90 mm hits the second threshold
    assert int(levels[0, 0]) == 255  # nodata
    with pytest.raises(ValueError):
        process.alert_level(acc, (100, 50))


def test_cog_zarr_and_stac(daily_tifs, tmp_path):
    da = process.load_stack(daily_tifs, DAYS)
    cog = process.write_cog(process.accumulate(da, 3), tmp_path / "cog" / "precip_3d.tif")
    with rasterio.open(cog) as src:
        assert src.profile["tiled"] and src.nodata == NODATA

    store = tmp_path / "rain.zarr"
    process.update_zarr(da.isel(time=slice(0, 2)), store)
    process.update_zarr(da, store)
    process.update_zarr(da, store)  # idempotent
    assert xr.open_zarr(store).sizes["time"] == 4

    item = stac.build_item(DAYS[-1], {"precip_3d": cog}, (50, 100, 150))
    href = stac.update_catalog(tmp_path / "stac", item, [35.0, 0.5, 35.5, 1.0], validate=False)
    stac.update_catalog(tmp_path / "stac", item, [35.0, 0.5, 35.5, 1.0], validate=False)  # replace, not duplicate
    collection = pystac.Collection.from_file(str(href))
    items = list(collection.get_items())
    assert [i.id for i in items] == ["chirps-gha-20260604"]
    assert not items[0].assets["precip_3d"].href.startswith("/")


def test_zonal_stats_and_classify(daily_tifs):
    acc = process.accumulate(process.load_stack(daily_tifs, DAYS), 3).compute()
    gdf = gpd.GeoDataFrame(
        {"shape_id": ["A", "B", "TINY"], "name": ["a", "b", "tiny"], "country_iso3": ["KEN"] * 3},
        geometry=[box(35.0, 0.5, 35.25, 1.0), box(35.25, 0.5, 35.5, 1.0), box(35.301, 0.701, 35.302, 0.702)],
        crs="EPSG:4326",
    )
    stats = zonal.zonal_stats(gdf, {"precip_3d": acc})
    assert stats.loc[1, "precip_3d_mean"] == pytest.approx(90)
    assert not np.isnan(stats.loc[2, "precip_3d_mean"])  # small district still gets a value
    levels = zonal.classify(pd.Series([10.0, 60.0, 200.0, np.nan]), (50, 100, 150))
    assert levels.tolist()[:3] == [0, 1, 3] and pd.isna(levels.iloc[3])
