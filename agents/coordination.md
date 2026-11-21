# Agent coordination

This file is committed to the repository: keep it short, neutral and technical.

Each agent works on its own branch and keeps **everything dynamic in its own
progress file on that branch**. Nobody edits another agent's files, so there
are no conflicts. Read another agent's status at any time without switching
branches:

```sh
git fetch origin
git show origin/agent3/cpu-governance:agents/progress/agent-3.md
```

| Agent | Branch | Progress file |
| --- | --- | --- |
| 1 | `agent1/import-safety` | `agents/progress/agent-1.md` |
| 2 | `agent2/map-precision` | `agents/progress/agent-2.md` |
| 3 | `agent3/cpu-governance` | `agents/progress/agent-3.md` |
| 4 | `agent4/evaluation` | `agents/progress/agent-4.md` |

## Progress file layout (same sections for everyone)

    # Agent N progress
    ## RESUME HERE      current step, last pushed commit, exact next command, half-done work, log paths
    ## Alerts           new misses or broken builds found in anyone's branch (newest first)
    ## Branch status    WIP or ready to merge; what changed; evidence (misses, simulated s, CPU vs main)
    ## Requests         "- to agent M: what, why, which file" (agent M answers in its own file: "DONE in <commit>")
    ## Datasets         name, location (path or Actions run + artifact), families, how ground truth was made
    ## Evaluations      branch @ commit vs main @ commit: split, misses, simulated, CPU per 30 commits
    ## Notes            findings, negative results, decisions

**Check the other three progress files at every checkpoint** (every 30
minutes) for Alerts and Requests addressed to you.

## Shared datasets

- seed100-full: Actions run `44941967905`, artifact `mutant-evaluation`. 2,400
  mixed mutants with subtest-aware ground truth.
