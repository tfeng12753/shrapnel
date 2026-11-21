# Keep the exact scorer Python/SQLite image.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SKIP_INTEGRATION=0 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
ARG BUILD_MAP=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN if [ "$BUILD_MAP" = "1" ]; then pip install --no-cache-dir -r /app/requirements.lock coverage==7.10.7; fi
COPY tools/fetch_source.py /app/tools/fetch_source.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/fetch_source.py; fi
COPY tools/build_map.py tools/instrument.py /app/tools/
COPY maps /app/maps
# Per-test pytest timeout while instrumenting; raise it for a one-off `make map`.
ARG MAP_TIMEOUT=180
# Maps use ONLY the unchanged pinned checkout, never sample patches/answers.
RUN if [ "$BUILD_MAP" != "1" ]; then :; \
    elif [ -f /app/maps/dependencies.json ]; then \
      python -c 'import gzip,pathlib; p=pathlib.Path("/app/maps/dependencies.json"); gzip.open("/app/dependencies.json.gz", "wb").write(p.read_bytes())'; \
    else \
      MAP_TIMEOUT=$MAP_TIMEOUT PYTHONPATH=/opt/sqlglot python /app/tools/build_map.py; \
    fi
COPY selector.py native_probes.py selector-config.json /app/
COPY tools/timed_run.py /app/tools/timed_run.py
ENV HOME=/tmp
USER 65534:65534
CMD ["python", "/app/selector.py"]
