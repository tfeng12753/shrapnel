IMAGE ?= blast-radius-entry
SAMPLE := $(CURDIR)/sample
OUTPUT := $(CURDIR)/output.jsonl

# The flags the scorer runs your image with. The scorer is linux/amd64, so on
# an Apple Silicon Mac --platform makes your local run match it.
RUN_FLAGS := --rm --platform linux/amd64 --network none --cpus 2 \
	--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
	--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
	--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
	--volume $(SAMPLE)/commits:/data/commits:ro \
	--volume $(SAMPLE)/tests.json:/data/tests.json:ro

.PHONY: build sample check

build:
	docker build --platform linux/amd64 --tag $(IMAGE) .

# Runs your image once on the sample commits under the scorer's limits and
# scores what it printed.
sample: build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT)

# The output's rules only: every line well formed and every commit answered.
# A wrong selection still passes here; make sample counts the misses.
check: build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT) > /dev/null && echo "format ok"

.PHONY: unit local-benchmark export-map map

unit:
	python3 -m unittest discover -s tests -v

# Does not reproduce Docker isolation. Without a map, measures the full-suite fallback.
local-benchmark:
	python3 tools/benchmark.py --report validation.json

# Export the already-validated build-time map as a plain, versionable file.
export-map:
	python3 tools/export_map.py --image $(IMAGE)

.PHONY: safe-sample

# Scheduling-only mode: no checkout, packages, or dependency-map build.
safe-sample:
	docker build --platform linux/amd64 --build-arg BUILD_MAP=0 --tag $(IMAGE) .
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT)

.PHONY: competitive

competitive:
	python3 tools/competitive.py --image $(IMAGE)

# One-off: build the map with a long per-test timeout (instrumented
# test_max_nodes exceeds 180 s), then export it to maps/dependencies.json.
# Commit that file so normal builds skip map generation.
map:
	docker build --platform linux/amd64 --build-arg MAP_TIMEOUT=3600 --tag $(IMAGE) .
	python3 tools/export_map.py --image $(IMAGE)
