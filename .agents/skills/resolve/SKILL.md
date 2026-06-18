---
name: resolve
description: Resolve every branch of a design tree into a complete plan — enumerate options, default to recommendations, no interactive Q&A. Use when the user wants a plan auto-resolved, mentions "resolve the design", "decide for me", "auto-plan", or wants grill-me without back-and-forth.
---

Resolve the user's plan into a complete, actionable design by walking the full decision tree and defaulting every branch to the recommended answer. No interactive Q&A — all branches are resolved in one pass.

## Steps

### 1. Map the decision tree

Read the user's plan. Identify every decision point — questions that have multiple plausible answers and whose resolution changes the implementation. Walk dependencies: a branch settled early shapes later branches, so order them by dependency, not by when they appear in the user's description.

If a question can be answered by exploring the codebase, explore the codebase instead.

Completion criterion: every decision point is identified, ordered by dependency, and no two branches remain tangled (each is independently resolvable).

### 2. Resolve each branch

For every decision point, present:

- The question
- Options (A, B, C...) — each concrete enough to implement, not abstract alternatives
- Recommended answer — with one-sentence justification

Default to the recommendation. The user can override any branch by name after reading the full plan.

Principles for choosing the recommendation:
- Prefer simpler over complex: standard library → platform feature → already-installed dependency → one line → minimum code
- Prefer what keeps the plan self-contained — no new external dependencies unless the plan demands one
- Prefer what the codebase already does — consistency over novelty
- When two options are equally good, pick the one with fewer moving parts

Completion criterion: every branch has options enumerated and a recommended answer chosen. No branch left as "either way works" without a default.

### 3. Present the resolved plan

Output the complete plan as a single, reviewable block:

1. A one-paragraph summary of the resolved design
2. The decision tree — each branch with its question, chosen option, and justification
3. The implementation order — what gets built/changed first, second, third, with file paths where known

The user can then:
- Say "go" or "proceed" to accept the plan as-is
- Override specific branches: "change #3 to option B"
- Reject and restart: "rethink the whole thing"

If the user overrides one branch, re-resolve only the downstream branches that depend on it, and re-present the affected portion.
