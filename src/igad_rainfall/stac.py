"""Publish pipeline outputs as a static STAC catalogue (one Item per day, one Asset per product)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pystac
import rasterio
from pystac.extensions.projection import ProjectionExtension
from shapely.geometry import box, mapping

COLLECTION_ID = "chirps-gha-daily"
COG_MEDIA = pystac.MediaType.COG


def _collection(bbox: list[float]) -> pystac.Collection:
    return pystac.Collection(
        id=COLLECTION_ID,
        title="CHIRPS daily rainfall products for the Greater Horn of Africa",
        description=(
            "Daily CHIRPS v2.0 rainfall clipped to the ICPAC domain, with 1/3/7/30-day accumulations "
            "and a heavy-rainfall alert level derived from the 3-day accumulation."
        ),
        extent=pystac.Extent(
            pystac.SpatialExtent([bbox]),
            pystac.TemporalExtent([[dt.datetime(1981, 1, 1, tzinfo=dt.UTC), None]]),
        ),
        license="CC-BY-4.0",
        providers=[
            pystac.Provider("Climate Hazards Center, UC Santa Barbara", roles=["producer", "licensor"],
                            url="https://www.chc.ucsb.edu/data/chirps"),
            pystac.Provider("igad-rainfall-pipeline", roles=["processor"]),
        ],
        keywords=["rainfall", "CHIRPS", "flood", "early warning", "East Africa", "IGAD", "ICPAC"],
    )


def build_item(day: dt.date, assets: dict[str, Path], thresholds: tuple[float, ...] = (),
               base_url: str = "") -> pystac.Item:
    """Create a STAC Item for one day; geometry and projection come from the first COG."""
    first = next(iter(assets.values()))
    with rasterio.open(first) as src:
        bounds, shape, transform, epsg = src.bounds, src.shape, src.transform, src.crs.to_epsg()
    item = pystac.Item(
        id=f"chirps-gha-{day:%Y%m%d}",
        geometry=mapping(box(*bounds)),
        bbox=list(bounds),
        datetime=dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.UTC),
        properties={"alert_thresholds_mm": list(thresholds)},
        collection=COLLECTION_ID,
    )
    proj = ProjectionExtension.ext(item, add_if_missing=True)
    proj.epsg, proj.shape, proj.transform = epsg, list(shape), list(transform)[:6]
    for key, path in assets.items():
        href = f"{base_url.rstrip('/')}/{path.name}" if base_url else str(path.resolve())
        item.add_asset(key, pystac.Asset(href=href, media_type=COG_MEDIA, roles=["data"],
                                         title=key.replace("_", " ")))
    return item


def update_catalog(stac_dir: Path, item: pystac.Item, bbox: list[float], validate: bool = True) -> Path:
    """Add or replace the day's Item in the collection and save it as a self-contained catalogue."""
    stac_dir = stac_dir.resolve()  # asset paths are resolved too, so relative hrefs stay short
    href = stac_dir / "collection.json"
    collection = pystac.Collection.from_file(str(href)) if href.exists() else _collection(bbox)
    if collection.get_item(item.id) is not None:
        collection.remove_item(item.id)
    collection.add_item(item)
    collection.update_extent_from_items()
    collection.normalize_hrefs(str(stac_dir))
    collection.make_all_asset_hrefs_relative()  # no-op for absolute http(s) hrefs
    if validate:  # fetches JSON schemas, so CI runs it and offline runs can skip it
        collection.validate_all()
    collection.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
    return href
