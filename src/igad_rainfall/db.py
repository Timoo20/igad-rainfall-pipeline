"""Load boundaries and daily district statistics into PostGIS."""
from __future__ import annotations

import datetime as dt
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS admin_boundaries (
    shape_id      text PRIMARY KEY,
    name          text NOT NULL,
    country_iso3  char(3) NOT NULL,
    geom          geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_boundaries_geom_idx ON admin_boundaries USING gist (geom);
CREATE TABLE IF NOT EXISTS rainfall_stats (
    date          date NOT NULL,
    shape_id      text NOT NULL REFERENCES admin_boundaries (shape_id),
    metric        text NOT NULL,
    value         double precision,
    PRIMARY KEY (date, shape_id, metric)
);
CREATE TABLE IF NOT EXISTS rainfall_alerts (
    date          date NOT NULL,
    shape_id      text NOT NULL REFERENCES admin_boundaries (shape_id),
    alert_level   smallint,
    precip_3d_p90 double precision,
    PRIMARY KEY (date, shape_id)
);
"""


def engine(url: str):
    return create_engine(url, pool_pre_ping=True)


def init_schema(url: str) -> None:
    with engine(url).begin() as conn:
        for stmt in filter(str.strip, SCHEMA_SQL.split(";")):
            conn.execute(text(stmt))


def load_boundaries(url: str, gdf: gpd.GeoDataFrame) -> int:
    """Insert boundaries that are not yet in the table (boundaries change rarely)."""
    eng = engine(url)
    with eng.connect() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT shape_id FROM admin_boundaries"))}
    new = gdf[~gdf.shape_id.isin(existing)].copy()
    if new.empty:
        return 0
    new["geometry"] = new.geometry.apply(lambda g: g if g.geom_type == "MultiPolygon" else MultiPolygon([g]))
    new = new.rename_geometry("geom")
    new.to_postgis("admin_boundaries", eng, if_exists="append", index=False)
    log.info("Loaded %d boundaries", len(new))
    return len(new)


def load_day(url: str, day: dt.date, stats: pd.DataFrame) -> int:
    """Replace one day's statistics and alerts in a single transaction (safe to re-run)."""
    metric_cols = [c for c in stats.columns if c.startswith("precip_")]
    long = stats.melt(id_vars=["shape_id"], value_vars=metric_cols, var_name="metric", value_name="value")
    long["date"] = day
    alerts = stats[["shape_id", "alert_level", "precip_3d_p90"]].assign(date=day)
    with engine(url).begin() as conn:
        conn.execute(text("DELETE FROM rainfall_stats WHERE date = :d"), {"d": day})
        conn.execute(text("DELETE FROM rainfall_alerts WHERE date = :d"), {"d": day})
        long.to_sql("rainfall_stats", conn, if_exists="append", index=False)
        alerts.to_sql("rainfall_alerts", conn, if_exists="append", index=False)
    log.info("Loaded %d stats rows and %d alerts for %s", len(long), len(alerts), day)
    return len(alerts)
