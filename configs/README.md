# Experiment Configs

YAML configuration files for experiments.

## Files (to be created)

| Config | What it defines | Owner |
|--------|----------------|-------|
| `scenarios.yaml` | The 8 fixed environment scenarios | Member B |
| `phase1_runs.yaml` | Phase 1: N levels, seeds, repeats | Member B |
| `phase2_interventions.yaml` | Phase 2: intervention parameters | Member B |
| `phase2_re_adaptation.yaml` | Phase 2: re-adaptation protocol settings | Member B |
| `detector_features.yaml` | Feature windows, indicators, formats | Member C |
| `detector_models.yaml` | Model hyperparameters | Member C |

## Example

```yaml
# phase1_runs.yaml
phase: 1
description: "Risk discovery by agent density"

density_levels: [5, 20, 50, 80, 100]
stratified_repeats:
  5: 6
  20: 6
  50: 12
  80: 12
  100: 12

scenarios:
  - normal-thick-early
  - normal-thick-late
  - normal-thin-early
  - normal-thin-late
  - stress-thick-early
  - stress-thick-late
  - stress-thin-early
  - stress-thin-late

n_steps: 100
step_minutes: 1.0
seeds: [0, 1, 2, 3, 4, 5, 6, 7]  # 8 seeds per condition

output_dir: results/phase1/
```
