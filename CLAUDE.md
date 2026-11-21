# Instructions for AI agents working in this repository

## Confidentiality (highest priority)

This repository is public. Everything you write that reaches GitHub (files,
commit messages, branch names, progress notes, PR titles, descriptions and
comments) must not reveal what this project is for or where it came from.

Never write, in any prose you add or change:

- the project's former name, or any name of an event, track, task or program
  it relates to;
- the name of the third-party library under test. Call it "the pinned
  library" or "the target library";
- the names, usernames or repository names of any other people or groups, or
  links to their work;
- any mention of competitions, challenges, contests, leaderboards, rankings,
  submissions, judges, organizers, hidden test sets, or scores from any
  external system;
- abbreviations or short forms of any of the above.

Use neutral wording instead: "sample patches", "held-out mutants",
"simulated seconds" or "cost", "container limits", "the selector image",
"the pinned checkout".

Unavoidable technical identifiers are the only exception: existing import
names, package and file paths (for example the pinned library's own module
paths), and the pinned revision hash. Don't add new prose around them that
names the library.

Before every commit, scan your diff and commit message for these terms and
remove them. If you're unsure whether something identifies the project, leave
it out.

## Working rules

- Fail safe: a missed failing test is far more expensive than an extra test.
- New selector behavior goes behind a config key until measured on held-out
  mutants (misses, simulated seconds and CPU against `main`).
- Never build mutant or sample outcomes into the selector.
- Commit and push small, frequent checkpoints so work isn't lost if a session
  ends.
