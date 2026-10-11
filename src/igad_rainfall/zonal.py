"""Administrative boundaries (geoBoundaries) and district-level rainfall statistics."""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import xarray as xr
from rasterio import features

from .config import GEOBOUNDARIES_API, Settings

log = logging.getLogger(__name__)


def load_boundaries(settings: Settings, timeout: int = 60) -> gpd.GeoDataFrame:
    """Download (once) and merge simplified admin boundaries for every configured country."""
    cache = settings.data_dir / "boundaries"
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    for iso3 in settings.countries:
        path = cache / f"{iso3}_{settings.admin_level}.geojson"
        if not path.exists():
            meta = requests.get(GEOBOUNDARIES_API.format(iso3=iso3, level=settings.admin_level), timeout=timeout)
            meta.raise_for_status()
            geo = requests.get(meta.json()["simplifiedGeometryGeoJSON"], timeout=timeout)
            geo.raise_for_status()
            path.write_bytes(geo.content)
            log.info("Downloaded %s %s boundaries", iso3, settings.admin_level)
        gdf = gpd.read_file(path)
        gdf["country_iso3"] = iso3
        frames.append(gdf)
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    gdf = gdf.rename(columns={"shapeID": "shape_id", "shapeName": "name"})
    return gdf[["shape_id", "name", "country_iso3", "geometry"]]


def _zone_labels(gdf: gpd.GeoDataFrame, template: xr.DataArray) -> tuple[np.ndarray, list[int]]:
    """Rasterise zones onto the rainfall grid (-1 = outside every zone).

    Also returns the zones that own no pixel (districts smaller than a ~5.5 km pixel); the caller
    samples those separately with all_touched=True so every district gets a value.
    """
    labels = features.rasterize(
        ((geom, i) for i, geom in enumerate(gdf.geometry)),
        out_shape=(template.sizes["y"], template.sizes["x"]), transform=template.rio.transform(),
        fill=-1, dtype="int32",
    )
    missing = sorted(set(range(len(gdf))) - set(np.unique(labels).tolist()))
    return labels, missing


def zonal_stats(gdf: gpd.GeoDataFrame, layers: dict[str, xr.DataArray]) -> pd.DataFrame:
    """Mean, 90th percentile and max of each layer for each zone."""
    template = next(iter(layers.values()))
    labels, missing = _zone_labels(gdf, template)
    shape, transform = labels.shape, template.rio.transform()
    small = {i: features.rasterize([(gdf.geometry.iloc[i], 1)], out_shape=shape, transform=transform,
                                   fill=0, dtype="uint8", all_touched=True).astype(bool) for i in missing}
    flat = labels.ravel()
    inside = flat >= 0
    out = pd.DataFrame(index=pd.RangeIndex(len(gdf), name="zone"))
    for name, layer in layers.items():
        grid = np.asarray(layer.values, dtype="float64")
        frames = [pd.DataFrame({"zone": flat[inside], "v": grid.ravel()[inside]})]
        frames += [pd.DataFrame({"zone": i, "v": grid[mask]}) for i, mask in small.items()]
        g = pd.concat(frames).dropna().groupby("zone")["v"]
        out[f"{name}_mean"] = g.mean()
        out[f"{name}_p90"] = g.quantile(0.9)
        out[f"{name}_max"] = g.max()
    result = pd.concat([gdf.drop(columns="geometry").reset_index(drop=True), out.reset_index(drop=True)], axis=1)
    return result.round(2)


def classify(values: pd.Series, thresholds: tuple[float, ...]) -> pd.Series:
    """Alert level 0..n from ascending thresholds; NaN stays NaN."""
    levels = np.digitize(values.to_numpy(dtype="float64"), thresholds).astype("float64")
    levels[values.isna().to_numpy()] = np.nan
    return pd.Series(levels, index=values.index).astype("Int64")
