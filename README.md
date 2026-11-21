# Held-out mutant data (evaluation only)

`seed100/`: 2,400 seeded mutants of the pinned checkout (`commits/*.patch`,
`manifest.json`) with full-suite ground truth per mutant (`results/*.json`,
subtest-aware). `results/baseline.json` is the unmodified checkout.

Never use these as selector input. Read with:

    git fetch origin data/mutants-seed100
    git archive FETCH_HEAD seed100 | tar -x
