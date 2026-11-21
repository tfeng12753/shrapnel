# Validation record

The development environment has Python 3.12.14 and no Docker executable or
container daemon. GitHub and PyPI source/package requests were denied by its
network policy. GitHub CLI has no authenticated host; the GitHub integration is
administratively disabled. No public repository was created and no competition
submission was made.

Completed:

- 41 standard-library unit and integration checks for scheduling, strict patch
  application, AST checks, test-ID remapping, fixture/source readers, constant
  dependency propagation, inherited settings, subprocess dependencies, and
  conservative fallbacks. New checks cover comparison boundary refinement,
  safe identity/equality cases, string Enum values, classmethod settings, deferred
  annotations, shadowed `getattr`, actual forked native probes, failing subtests,
  teardown errors, and timeouts.
- All 30 provided patches run through the real selector in map-unavailable mode.
- Official `score.py` validation: 1104.510 simulated seconds, zero missed commits.
- Three local subprocess runs with measured user+system CPU and median scoring.
- Output size: 210,840 bytes, comfortably below 2 MB.

Not completed:

- Docker image build and exact restricted-container execution.
- Pinned sqlglot test collection with the supplied locked dependencies.
- Coverage and attribute instrumentation on sqlglot's actual suite.
- Build wall time and peak-memory verification.
- Sample or unseen-patch miss/score measurement with a real dependency map.

The benchmark in `validation.json` measures the scheduling-only fallback. It
must not be presented as a result for the dependency-based selector. Run
`make sample` on a network-enabled Docker host to establish that result. If the
map build fails, the build deliberately stops rather than shipping a map made
from an incomplete or failing baseline run. `Dockerfile.safe` provides the
scheduling-only alternative described in the README.

An experimental naming-only selector was rejected: it cost 178.584 simulated
seconds before penalties but missed six sample commits, for 1978.584 seconds
with penalties. It is not used or included in the submission. No per-patch or
per-mutation failure rules were added in response to those misses.

The new source/operand observers and bounded test verifier have not produced a
new sqlglot score in this environment. The only measured submission score here
remains the full-suite scheduling fallback. `make competitive` now automates the
missing real-image experiment, including feature ablations, three runs per
variant, CPU measurement, global configuration selection, and evidence export.
