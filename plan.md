# Parallel selector work

Control revision: `1ea65b5`. Both branches start with the same planning commit. Each owner keeps measurements and resume notes in their own file under `agents/progress/`. Tag every measurement with its revision and configuration.

| Owner | Branch | First experiment | Follow-up work |
| --- | --- | --- | --- |
| Agent 5 | `codex/agent-5-verification` | Measure full-test verification of the longest selected item against its marginal schedule saving | Full-query rule replay, planner and table-input checks, fixture verification |
| Agent 6 | `codex/agent-6-selection` | Establish why inherited required-key metadata edits still expand selection | Discarded strings, missing comparison evidence, dictionary key reads, dialect parse replay |

Read the corresponding kickoff under `agents/`. Agent 5 owns runtime probes and their options. Agent 6 owns static classifiers and their options. In shared files, touch only the option or call site for your feature. Keep replay capture and new tests in owner-specific modules. Preserve independent reasons a test was selected on mixed patches.

## Measurement and integration

1. Run the control and each toggle separately on identical sample patches and held-out mutants. Generate fresh failure-bearing controls for each proposed exclusion. Record missed patches and failing test IDs, simulated seconds, and selector CPU including unsuccessful attempts.
2. Compare the combined configuration on fresh batches of roughly 45 patches under container limits. Repeat timings, count each distinct patch once for correctness, and report median total cost, fraction of batches with a miss, and worst batch. Keep related mutant families together when interpreting uncertainty.
3. Keep experiments off by default. Report the 300-second per-missed-patch cost alongside schedule and CPU, including for deliberately aggressive offline variants. Keep the existing import-order regression in the control suite. Preserve worker packing.
4. When both owners have published tested checkpoints, create a separate integration branch from Agent 5 and merge Agent 6 there. Re-run the control, individual toggles and combination on the same inputs. Delivery for this phase ends at the two work branches.
