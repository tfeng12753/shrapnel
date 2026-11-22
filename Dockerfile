# Pinned Python/SQLite base image.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SKIP_INTEGRATION=0 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
ARG BUILD_MAP=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN if [ "$BUILD_MAP" = "1" ]; then pip install --no-cache-dir -r /app/requirements.lock coverage==7.10.7; fi
COPY tools/fetch_source.py /app/tools/fetch_source.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/fetch_source.py; fi
COPY tools/build_map.py tools/instrument.py tools/setup_flow.py tools/map_fingerprint.py /app/tools/
COPY maps /app/maps
# Per-test pytest timeout while instrumenting; raise it for a one-off `make map`.
ARG MAP_TIMEOUT=180
# FORCE_MAP=1 ignores the cached map and regenerates it (`make map`).
ARG FORCE_MAP=0
# Maps use ONLY the unchanged pinned checkout, never sample patches/answers.
# A cached map is used only if its metadata matches this builder; otherwise the build fails.
RUN if [ "$BUILD_MAP" != "1" ]; then :; \
    elif [ "$FORCE_MAP" != "1" ] && [ -f /app/maps/dependencies.json.gz ]; then \
      python /app/tools/map_fingerprint.py --root /app --check /app/maps/dependencies.meta.json \
        --map /app/maps/dependencies.json.gz && \
      cp /app/maps/dependencies.json.gz /app/dependencies.json.gz; \
    elif [ "$FORCE_MAP" != "1" ] && [ -f /app/maps/dependencies.json ]; then \
      python -c 'import gzip,pathlib; p=pathlib.Path("/app/maps/dependencies.json"); gzip.open("/app/dependencies.json.gz", "wb").write(p.read_bytes())'; \
    else \
      MAP_TIMEOUT=$MAP_TIMEOUT PYTHONPATH=/opt/sqlglot python /app/tools/build_map.py; \
    fi
# Files `import sqlglot` executes or reads; narrows the lazy-load test.
COPY tools/import_closure.py /app/tools/import_closure.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/import_closure.py; fi
# What each module's import runs (detects patches that break test collection).
COPY tools/import_time.py /app/tools/import_time.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/import_time.py --source /opt/sqlglot --output /app/import-time.json; fi
# Precompiled bytecode: forked and isolated checks otherwise recompile the library on every run.
RUN if [ "$BUILD_MAP" = "1" ]; then python -m compileall -q --invalidation-mode checked-hash /opt/sqlglot/sqlglot /opt/sqlglot/tests; fi
# Executor code generation on the pinned checkout (generation_check.py; about 1 minute).
COPY generation_check.py fixture_replay.py native_probes.py /app/
COPY tools/generation_record.py /app/tools/generation_record.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/generation_record.py --source /opt/sqlglot --output /app/generation-record.json.gz \
      || rm -f /app/generation-record.json.gz; fi
# Operands reaching value comparisons, per test (value_check.py; about 3 minutes).
COPY tools/value_record.py /app/tools/value_record.py
RUN if [ "$BUILD_MAP" = "1" ]; then python /app/tools/value_record.py --source /opt/sqlglot --output /app/value-record.json.gz \
      > /dev/null || rm -f /app/value-record.json.gz; fi
COPY tools/map_executor_plan.py tools/map_executor_screen.py tools/prepare_executor_plans.py /app/tools/
ARG BUILD_EXECUTOR_PLANS=0
RUN if [ "$BUILD_MAP" = "1" ] && [ "$BUILD_EXECUTOR_PLANS" = "1" ]; then python /app/tools/prepare_executor_plans.py; fi
COPY selector.py native_probes.py import_effects.py fixture_replay.py generation_check.py value_check.py fixture-entries.json.gz executor_fixtures.py executor_plans.py isolated_pytest.py selector-config.json /app/
COPY tools/timed_run.py /app/tools/timed_run.py
ENV HOME=/tmp
USER 65534:65534
CMD ["python", "/app/selector.py"]
