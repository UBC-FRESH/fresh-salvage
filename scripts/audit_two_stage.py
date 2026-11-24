#!/usr/bin/env python
"""Audit the two-stage principal/agent accounts across RH subsidy anchors.

For each run root this script:

* re-parses every step-state ARE with the principal's cohort parser;
* computes the stage-1 anticipated acceptance ``S^a`` as the offer-weighted
  available burned volume ``B[y-1] + I[y]`` (the same basis as the agent's
  salvage cap);
* reads realized salvage/green from the RH manifest; and
* writes the stage-1 index ``Z^a``, realized fiscal ``F``, agent profit, joint
  ``F + A``, the anticipated/realized subsidy, and the anticipation gap.

It also checks that cumulative realized salvage never exceeds cumulative
anticipated acceptance per cohort. The output is a CSV plus a JSON summary.

Usage::

    python scripts/audit_two_stage.py \
        outputs/corrected_landbase/rh_two_stage_0_seeded.yaml \
        outputs/corrected_landbase/rh_two_stage_21.5_seeded.yaml \
        ...
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import pandas as pd
import yaml

from fresh_salvage import principal
from fresh_salvage.models import RHRunConfig


def _manifest(root: Path) -> dict:
    matches = sorted(glob.glob(str(root / "manifests" / "*manifest.json")))
    if not matches:
        raise FileNotFoundError(f"no RH manifest under {root}")
    return json.loads(Path(matches[-1]).read_text(encoding="utf-8"))


def _anticipated(root: Path, cfg: RHRunConfig) -> tuple[float, list[float], float]:
    """Return ``(S^a, per-step S^a, max per-cohort cumulative violation)``."""

    economics = cfg.economics()
    stands = pd.read_parquet(cfg.stands_path)
    price_by_dt = principal._development_type_economics(
        stands,
        green_prices=economics.green_prices,
        burned_price_discount=economics.burned_price_discount,
    )
    curves = principal._yield_curves(Path(cfg.yields_path))

    total = 0.0
    per_step: list[float] = []
    violations: dict[tuple[str, int], float] = {}
    for step in range(1, cfg.steps + 1):
        are = root / "derived" / "rh_state" / f"step_{step - 1:02d}.are"
        offer_files = sorted(
            glob.glob(str(root / "data" / f"*step-{step:02d}-offers.parquet"))
        )
        if not are.exists() or not offer_files:
            break
        cohorts = principal._parse_are_cohorts(
            are,
            price_by_dt,
            curves,
            economics=economics,
            burn_rate_multiplier=cfg.burn_rate_multiplier,
        )
        by_id = {cohort.cohort_id: cohort for cohort in cohorts}
        offers = pd.read_parquet(offer_files[-1])
        step_total = 0.0
        for cohort_id, year, fraction in zip(
            offers["cohort_id"], offers["year"], offers["offer_fraction"]
        ):
            cohort = by_id[str(cohort_id)]
            coefficients = principal._principal_offer_coefficients(
                cohort,
                cfg.period_length,
                cfg.decay_rate,
                period_length=cfg.period_length,
                grade_clock_period=cfg.period_length,
                burned_stock_reset=cfg.burned_stock_reset,
                include_influx=True,
            )
            index = int(year) - 1
            if not 0 <= index < len(coefficients.annual_burned_volume_m3):
                continue
            anticipated = float(fraction) * coefficients.annual_burned_volume_m3[index]
            step_total += anticipated
            key = (str(cohort_id), step)
            violations[key] = violations.get(key, 0.0) + anticipated
        total += step_total
        per_step.append(step_total)

        decision_files = sorted(
            glob.glob(str(root / "data" / f"*step-{step:02d}-decisions.parquet"))
        )
        if decision_files:
            decisions = pd.read_parquet(decision_files[-1])
            realized = decisions.groupby("cohort_id")["salvage_volume_m3"].sum()
            for cohort_id, value in realized.items():
                key = (str(cohort_id), step)
                violations[key] = violations.get(key, 0.0) - float(value)

    max_violation = max(
        (value for value in violations.values() if value > 0.0), default=0.0
    )
    return total, per_step, max_violation


def _audit(config_path: Path) -> dict[str, float | str]:
    cfg = RHRunConfig.model_validate(yaml.safe_load(config_path.read_text()))
    root = Path(cfg.output_root)
    manifest = _manifest(root)
    anticipated, per_step, max_violation = _anticipated(root, cfg)

    realized = float(manifest["total_burned_harvest_m3"])
    green = float(manifest["total_green_harvest_m3"])
    gamma = float(cfg.subsidy_rate_per_m3)
    fiscal = 15.0 * green + 0.25 * realized - gamma * realized
    agent = float(manifest["total_agent_objective_base_year"])
    return {
        "run_id": cfg.run_id,
        "gamma": gamma,
        "anticipated_salvage_m3": anticipated,
        "realized_salvage_m3": realized,
        "acceptance_pct": 100.0 * realized / anticipated if anticipated else 0.0,
        "realized_green_m3": green,
        "stage1_objective": float(manifest["total_principal_objective"]),
        "anticipated_subsidy": gamma * anticipated,
        "realized_subsidy": gamma * realized,
        "subsidy_gap": gamma * (anticipated - realized),
        "fiscal_F": fiscal,
        "agent_A": agent,
        "joint_J": fiscal + agent,
        "max_cohort_violation_m3": max_violation,
        "per_step_anticipated_m3": per_step,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/corrected_landbase/two_stage_audit.csv"),
    )
    args = parser.parse_args()

    rows = [_audit(path) for path in args.configs]
    frame = pd.DataFrame(rows)
    base = frame.loc[frame["gamma"].idxmin()]
    frame["fiscal_cost_vs_zero"] = frame["fiscal_F"] - base["fiscal_F"]
    frame["joint_cost_vs_zero"] = frame["joint_J"] - base["joint_J"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    summary = args.out.with_suffix(".json")
    summary.write_text(
        json.dumps(rows, indent=2) + "\n",
        encoding="utf-8",
    )
    columns = [
        "gamma",
        "anticipated_salvage_m3",
        "realized_salvage_m3",
        "acceptance_pct",
        "stage1_objective",
        "fiscal_F",
        "agent_A",
        "joint_J",
        "fiscal_cost_vs_zero",
        "joint_cost_vs_zero",
        "max_cohort_violation_m3",
    ]
    print(frame[columns].to_string(index=False))
    print(f"\nwrote {args.out} and {summary}")


if __name__ == "__main__":
    main()
