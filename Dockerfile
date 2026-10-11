FROM python:3.12-slim

# rasterio/geopandas wheels bundle GDAL, so no system GDAL is needed.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[db]" \
 && useradd --create-home --uid 1000 pipeline \
 && mkdir -p /data && chown pipeline /data
USER pipeline
VOLUME ["/data"]
ENTRYPOINT ["igad-rainfall"]
