---
name: resolve
description: Resolve every branch of a design tree into a complete plan — enumerate options, default to recommendations, no interactive Q&A. Use when the user wants a plan auto-resolved, mentions "resolve the design", "decide for me", "auto-plan", or wants grill-me without back-and-forth.
---

Resolve the user's plan into a complete, actionable design by walking the full decision tree and defaulting every branch to the recommended answer. No interactive Q&A — all branches are resolved in one pass.

## Steps

### 0. Deep Codebase Exploration (Mandatory Pre-flight)

Before making ANY decision, you MUST fully understand the existing codebase. Never recommend based on guesswork.

Must read or explore:

- Project config files (package.json / pyproject.toml / tsconfig.json / go.mod / Cargo.toml) — understand existing tech stack and dependencies
- Entry point files and core modules — understand architecture and code organization
- Design docs (AGENTS.md / DESIGN.md / README.md / .cursorrules) — understand project conventions and design intent
- Files directly relevant to the user's request — understand existing patterns and constraints

Completion criterion: You have mastered the project's tech stack, code style, module structure, existing dependencies, and design conventions. Every recommendation must reference specific facts discovered in the codebase.

### 1. Map the Decision Tree

Read the user's plan. Identify every decision point — questions that have multiple plausible answers and whose resolution changes the implementation.

Order by **dependency**: a branch settled early shapes later branches, so order them by dependency, not by when they appear in the user's description. Draw dependency chains: A → B means B cannot be decided before A is settled.

**Completeness Self-Check** (mandatory after identification): Scan these decision categories one by one to ensure nothing is missed:

- Architecture: tech stack, project structure, module division, routing design
- Data: data models, storage, API contracts, state management
- Flow: core pipeline, error handling, edge cases, fallback strategies
- Engineering: testing strategy, build tooling, CI/CD, deployment
- UX/CLI: CLI/UI framework, configuration management, progress reporting, logging
- Security & Compliance: auth/authz, data privacy, licensing

Completion criterion: Every decision point identified, ordered by dependency with dependency chains noted, no tangled branches, all 6 categories scanned.

### 2. Resolve Each Branch

For every decision point, present:

- **The question** — one sentence describing what this decision solves
- **Options** (A, B, C...) — each concrete enough to implement, not abstract alternatives
  - When options involve multi-dimensional tradeoffs (≥3 options OR ≥3 evaluation dimensions), you **MUST use a comparison table**: columns like Option | Pros | Cons | Best For
- **Recommended answer** — with **2-4 sentence justification** covering: why this choice, why the others were rejected, how it relates to the existing codebase
- **Risk flag** (if applicable) — if this decision has significant fragility (e.g., depends on a newly-released library, conflicts with future plans, requires special user permissions), mark with ⚠️

Principles for choosing the recommendation:
- Prefer simpler over complex: standard library → platform feature → already-installed dependency → one line → minimum code
- Prefer what keeps the plan self-contained — no new external dependencies unless the plan demands one
- Prefer what the codebase already does — consistency over novelty, citing specific evidence from the exploration phase
- When two options are equally good, pick the one with fewer moving parts

**When NOT to default-decide**: If a decision meets ANY of these criteria, do NOT pick a default. Instead, mark it as **「Needs User Decision」** with an explanation:
- The options are nearly equivalent (genuine tradeoff, no clear winner)
- The decision involves personal preference (editor, color scheme, UI framework style)
- The decision has major long-term impact (database choice, programming language, license)
- Critical information is missing and prevents judgment

Completion criterion: Every branch has options enumerated and a recommended answer chosen (or explicitly flagged as needing user decision). No branch left as "either way works" without a default.

### 3. Self-Review Gate (Mandatory Before Output)

Before presenting the plan, you MUST pass these checks:

1. **Internal consistency**: Scan all decisions pairwise. Are there contradictions? (e.g., chose Python but CLI framework recommendation is commander.js → contradiction!)
2. **Dependency chain closure**: Every decision's dependencies were decided before it. No circular dependencies ("chicken-and-egg").
3. **Coverage re-check**: Return to the 6 categories from Step 1. Confirm no category was missed.
4. **Codebase conflict check**: Does any recommendation contradict facts discovered in the exploration phase? If so, explicitly explain why you're deviating from existing patterns.

If self-review finds issues, return to Step 2, re-decide, then self-review again until passing.

Completion criterion: All consistency checks pass, no missed categories, no codebase conflicts.

### 4. Present the Resolved Plan

Output the complete plan as a single, reviewable block:

1. **One-paragraph summary** (3-5 sentences) — the user should understand the design in 30 seconds
2. **Decision tree** — each branch numbered by dependency order, with:
   - Question
   - Comparison table (where applicable)
   - Chosen option
   - Full justification
   - Risk flag (where applicable)
3. **Items needing user decision** (if any) — listed separately, with pros/cons for each option
4. **Implementation order** — what to build/change first, second, third, with file paths and estimated effort where known

The user can then:
- Say "go" or "proceed" to accept the plan as-is
- Override specific branches: "change #3 to option B"
- Resolve flagged decisions: "for #7, go with option A"
- Reject and restart: "rethink the whole thing"

If the user overrides one branch, re-resolve only the downstream branches that depend on it, and re-present the affected portion.
