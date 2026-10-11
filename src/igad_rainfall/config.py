"""Pipeline settings. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Greater Horn of Africa domain used by ICPAC (11 Member States), as (west, south, east, north).
GHA_BBOX = (21.8, -11.8, 51.5, 23.3)

# ISO3 codes: the 8 IGAD Member States plus Burundi, Rwanda and Tanzania (ICPAC's wider domain).
GHA_COUNTRIES = ["DJI", "ERI", "ETH", "KEN", "SOM", "SSD", "SDN", "UGA", "BDI", "RWA", "TZA"]

CHIRPS_DAILY_URL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/cogs/p05/"
    "{year}/chirps-v2.0.{year}.{month:02d}.{day:02d}.cog"
)
CHIRPS_YEAR_INDEX = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/cogs/p05/{year}/"
GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{level}/"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    # Accumulation windows in days, e.g. "1,3,7,30".
    windows: tuple[int, ...] = field(
        default_factory=lambda: tuple(int(w) for w in _env("ACCUM_WINDOWS", "1,3,7,30").split(","))
    )
    # Heavy-rainfall alert thresholds (mm) applied to the 3-day accumulation.
    alert_thresholds: tuple[float, float, float] = field(
        default_factory=lambda: tuple(float(t) for t in _env("ALERT_THRESHOLDS_MM", "50,100,150").split(","))
    )
    countries: tuple[str, ...] = field(
        default_factory=lambda: tuple(_env("COUNTRIES", ",".join(GHA_COUNTRIES)).split(","))
    )
    admin_level: str = field(default_factory=lambda: _env("ADMIN_LEVEL", "ADM1"))
    # SQLAlchemy URL for PostGIS; empty disables the database load step.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    # Public base URL that STAC asset hrefs point to (e.g. a GCS/S3 bucket). Empty = relative hrefs.
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", ""))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cog_dir(self) -> Path:
        return self.data_dir / "cog"

    @property
    def zarr_path(self) -> Path:
        return self.data_dir / "zarr" / "chirps_gha_daily.zarr"

    @property
    def stac_dir(self) -> Path:
        return self.data_dir / "stac"
