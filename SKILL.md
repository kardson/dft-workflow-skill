---
name: vasp-dft-workflow
description: Guide VASP research tasks through an evidence-based six-step workflow, from environment checks and research planning to authorized execution and result review. Use when an agent helps prepare, run, or analyze VASP calculations; not a substitute for scientific decisions, local authorization, or a VASP installation.
metadata:
  short-description: Evidence-based VASP research workflow
---

# VASP DFT workflow

Help the researcher express the scientific goal in natural language while you handle workflow records, command syntax, and evidence links. Treat this skill as a guide to the public tools and templates shipped beside this file. The public repository root is the skill root; keep the complete directory together when installing or moving it. Resolve every tool, document, and template from the absolute directory containing this `SKILL.md`, never from the current working directory.

## First project connection

When first used in a selected research workspace, read [project connection](docs/skill_installation.md#首次项目接入与自动使用). Check existing project instructions for an equivalent rule. If absent, show the proposed project-local default-routing block and obtain permission to merge it into that workspace's AGENTS.md, unless the user has already authorized that concrete change. Installation alone is not authorization to edit a research project. Preserve all existing rules; never rewrite or duplicate the file or change unrelated routes. If permission is declined or the file is read-only, continue the requested work without persisting a rule. Once connected, reuse the rule without asking again. Simple progress queries must not be delayed by onboarding.

## Route each request

- For a progress question, use only the one-time, read-only progress route described in [the run and delivery guide](docs/capabilities.md#运行与交付节奏). Do not start polling or background monitoring.
- Before using tools for planning, parameter review, or metrics, select an independent user-owned research workspace and set `DFT_WORKSPACE_ROOT` explicitly. Keep research inputs, outputs, and records there; never write scientific work into the installed skill directory.
- For a new calculation, follow the six steps below. For a planning-only, input-review, failure-diagnosis, or result-analysis request, enter at the relevant step and do not force unrelated steps.
- For tool use, inspect the selected tool's `--help` and only the relevant contract or template. Tools are in `tools/`; templates are in `templates/`. Do not guess command arguments or require the researcher to operate scripts.
- Read [the capability index](docs/capabilities.md) when choosing an entry point. Read [the detailed workflow](docs/standard_workflow.md) for complete calculations, [the getting-started guide](docs/getting_started.md) for environment and workspace setup, or [the evidence verifier guide](docs/evidence-verifier.md) when a result's provenance or analysis state is unclear.

## Six steps

1. **Environment first.** For a new workflow, verify the complete standard environment before planning execution: Python/tool dependencies, VASP build features, pseudopotentials, resources, and the target workstation/run directory. Tool-side checks do not prove the workstation is ready. Mark each observed gap `MISSING` or `UNVERIFIED` and name its evidence gap; do not launch a calculation to stand in for an environment check.
2. **Clarify the research question and materials.** Ask about every unresolved scientific choice before treating it as settled. Record actual answers and distinguish available, located, obtained, and missing materials. A search result does not mean the material was obtained. If material is unavailable or access is restricted, give the researcher a concrete supplement list and state what each item is needed for. Recommendations may be offered with their basis, but never record a recommendation as the researcher's decision.
3. **Develop and confirm a plan.** Explain methods, model, dependencies, precision comparisons, resource cost, deliverables, and stop conditions. The researcher confirms scientific choices and acceptance criteria. A structurally complete proposal is not scientific approval.
4. **Prepare and check inputs.** Prepare a complete candidate package and compare its actual files, species/order, masks, numerical settings, pseudopotential identity, and fresh/restart contract with the confirmed plan. Fix mechanical issues within scope. Route scientific changes back for a decision. Save and pass the real approval record into preparation checks; do not substitute a synthetic, missing, or merely suggested approval.
5. **Execute and collect evidence.** Execute only within explicit user authorization for the concrete scope. Confirm genuine progress, report the launch evidence, and end the turn. Do not wait, poll, or monitor continuously. Query status only when the user asks or a specific check was explicitly arranged. At a terminal state, collect the approved outputs and retain missing evidence as PARTIAL or UNKNOWN.
6. **Analyze and review.** Save and pass the real preparation record into analysis. If it is missing, say the required preparation audit is missing and block result delivery until it is completed; do not bypass the check. Keep actual output requirements bound to the approved contract; never weaken later requirements to make an incomplete result pass. Check actual outputs, completeness, settings, convergence, and provenance. Separate observed facts, interpretation, limitations, and researcher acceptance. If a parser fails, times out, or exits abnormally, mark analysis FAILED/UNAVAILABLE and do not present it as a completed result. A successful process exit does not establish convergence or scientific validity. Re-check source evidence before delivery; if it changed, reacquire and analyze it again.

## Boundaries

Local planning, preparation, review, and diagnosis do not grant execution permission. Submission, remote writes/transfers, restart, automated repair, and continuing monitoring need explicit authorization covering that action; preserve any already granted authorization within its stated scope. Do not choose missing scientific methods, species, constraints, reference states, or restart contracts on the researcher's behalf. Report observed environment gaps as `MISSING` or `UNVERIFIED`, with the concrete evidence or check that is absent. Keep the installed skill directory read-only for research work, resolve workspace paths from their selected absolute root, and quote paths when invoking commands if the path contains spaces. Preserve existing data and unrelated changes. Do not expose licensed POTCAR contents or credentials.

These tools support reproducible workflow records and evidence checks; they do not guarantee scientific correctness, convergence, or acceptance. A working VASP deployment, licensed inputs, appropriate compute resources, and relevant Python dependencies remain necessary. See [reproducing the analysis environment](docs/reproduce-clean-science.md) and [third-party notices](THIRD_PARTY.md) for dependencies and limits.
