# Experiment Configurations

This directory contains the versioned configurations that define experiments. A run artifact should record the config version/hash, code revision, scenario/path ID, environment seed, trader-assignment seed, and (for LLM runs) provider/model/prompt version.

## Current configuration status

No final Phase 1 experiment config is approved yet. The current `perp_sim` defaults and the 50-scripted-bot calibration scripts are environment-development settings; they are not the final 100-trader Phase 1 design.

Create and review these configs before their phase begins:

| Config | Required contents |
|---|---|
| `environment.yaml` | Market mechanics, default parameter version, units and time convention |
| `scenarios.yaml` | Explicit scenario factors and path source; use `*-synthetic` and `*-replay` scenario names |
| `calibration.yaml` | 50-bot population, role mix, capital budget, training/held-out data and acceptance bands |
| `phase1_replacement.yaml` | 100 account slots, role counts, fixed aggregate collateral, replacement shares, seeds/repeats and outcomes |
| `phase1_density.yaml` | Optional secondary population-size study and its capital/depth scaling rule |
| `phase2_interventions.yaml` | Intervention parameters and risk/liquidity success measures |
| `phase2_re_adaptation.yaml` | Adaptation mechanism, horizon and paired-run protocol |
| `detector_features.yaml` | Observable inputs, feature windows, leakage-safe availability times |
| `detector_models.yaml` | Splits by run/path, model settings and evaluation metrics |

The approved 100-trader population, total collateral budget and per-role allocations are design decisions still to be frozen; do not infer them from placeholder examples.
