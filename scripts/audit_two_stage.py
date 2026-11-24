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

from fresh_salvage import agent, principal
from fresh_salvage.models import RHRunConfig


def _manifest(root: Path) -> dict:
    matches = sorted(glob.glob(str(root / "manifests" / "*manifest.json")))
    if not matches:
        raise FileNotFoundError(f"no RH manifest under {root}")
    return json.loads(Path(matches[-1]).read_text(encoding="utf-8"))


def _anticipated(
    root: Path, cfg: RHRunConfig
) -> tuple[float, list[float], float, float, float]:
    """Return ``(S^a, per-step S^a, max overrun, max unrealized, A_undisc)``.

    The per-cohort comparison sums offered available burned volume and realized
    salvage within each step. ``max overrun`` is positive when realized salvage
    exceeds the anticipated base (a cap failure); ``max unrealized`` is the
    largest positive anticipation gap. Under the offered-share cap the overrun
    should be zero. ``A_undisc`` is the realized agent profit on the same
    undiscounted base-year basis as the fiscal account; it is evaluated from
    the decisions and the cohort margins, not from the discounted LP objective.
    """

    economics = cfg.economics()
    stands = pd.read_parquet(cfg.stands_path)
    price_by_dt = principal._development_type_economics(
        stands,
        green_prices=economics.green_prices,
        burned_price_discount=economics.burned_price_discount,
    )
    agent_price_by_dt = agent._development_type_economics(
        stands,
        green_prices=economics.green_prices,
        burned_price_discount=economics.burned_price_discount,
    )
    curves = principal._yield_curves(Path(cfg.yields_path))

    total = 0.0
    undiscounted = 0.0
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
        agent_cohorts = agent._parse_are_cohorts(
            are,
            agent_price_by_dt,
            curves,
            burn_rate_multiplier=cfg.burn_rate_multiplier,
        )
        cohorts_by_id = {cohort.cohort_id: cohort for cohort in agent_cohorts}
        coefficients_by_id = {
            cohort.cohort_id: principal._principal_offer_coefficients(
                cohort,
                cfg.period_length,
                cfg.decay_rate,
                period_length=cfg.period_length,
                grade_clock_period=cfg.period_length,
                burned_stock_reset=cfg.burned_stock_reset,
                include_influx=True,
            )
            for cohort in cohorts
        }
        offers = pd.read_parquet(offer_files[-1])
        step_total = 0.0
        for cohort_id, year, fraction in zip(
            offers["cohort_id"], offers["year"], offers["offer_fraction"]
        ):
            coefficients = coefficients_by_id[str(cohort_id)]
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
            gamma = float(cfg.subsidy_rate_per_m3)
            for row in decisions.itertuples(index=False):
                cohort = cohorts_by_id[row.cohort_id]
                green_margin = (
                    cohort.green_price_m3
                    - economics.green_harvest_cost
                    - economics.green_transport_cost_per_m3
                    - economics.green_stumpage_rate
                )
                burned_prices = agent._annual_burned_prices(
                    cohort, cfg.period_length, None
                )
                salvage_margin = (
                    burned_prices[int(row.year) - 1]
                    - economics.burned_harvest_cost
                    - economics.burned_transport_cost_per_m3
                    - economics.burned_stumpage_rate
                    + gamma
                )
                undiscounted += (
                    green_margin * float(row.harvest_volume_m3)
                    + salvage_margin * float(row.salvage_volume_m3)
                )

    max_overrun = max(
        (-value for value in violations.values() if value < 0.0), default=0.0
    )
    max_unrealized = max(
        (value for value in violations.values() if value > 0.0), default=0.0
    )
    return total, per_step, max_overrun, max_unrealized, undiscounted


def _audit(config_path: Path) -> dict[str, float | str]:
    cfg = RHRunConfig.model_validate(yaml.safe_load(config_path.read_text()))
    root = Path(cfg.output_root)
    manifest = _manifest(root)
    (
        anticipated,
        per_step,
        max_overrun,
        max_unrealized,
        agent_undiscounted,
    ) = _anticipated(root, cfg)

    realized = float(manifest["total_burned_harvest_m3"])
    green = float(manifest["total_green_harvest_m3"])
    gamma = float(cfg.subsidy_rate_per_m3)
    fiscal = 15.0 * green + 0.25 * realized - gamma * realized
    agent = float(manifest["total_agent_objective_base_year"])
    realized_subsidy = gamma * realized
    burned_stumpage = 0.25 * realized
    return {
        "run_id": cfg.run_id,
        "discount": float(cfg.burned_price_discount),
        "gamma": gamma,
        "anticipated_salvage_m3": anticipated,
        "realized_salvage_m3": realized,
        "acceptance_pct": 100.0 * realized / anticipated if anticipated else 0.0,
        "realized_green_m3": green,
        "stage1_objective": float(manifest["total_principal_objective"]),
        "anticipated_subsidy": gamma * anticipated,
        "realized_subsidy": realized_subsidy,
        "subsidy_gap": gamma * (anticipated - realized),
        "fiscal_F": fiscal,
        "agent_A_discounted": agent,
        "agent_A_undiscounted": agent_undiscounted,
        "joint_J_discounted": fiscal + agent,
        "joint_J_undiscounted": fiscal + agent_undiscounted,
        "burned_stumpage": burned_stumpage,
        "max_cohort_overrun_m3": max_overrun,
        "max_cohort_unrealized_m3": max_unrealized,
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
    frame["joint_cost_vs_zero"] = (
        frame["joint_J_undiscounted"] - base["joint_J_undiscounted"]
    )
    frame["agent_gain_vs_zero"] = (
        frame["agent_A_undiscounted"] - base["agent_A_undiscounted"]
    )
    frame["agent_gain_discounted_vs_zero"] = (
        frame["agent_A_discounted"] - base["agent_A_discounted"]
    )
    frame["fiscal_cost_per_m3"] = -frame["fiscal_cost_vs_zero"] / frame[
        "realized_salvage_m3"
    ].where(frame["realized_salvage_m3"] > 0.0)
    frame["joint_cost_per_m3"] = -frame["joint_cost_vs_zero"] / frame[
        "realized_salvage_m3"
    ].where(frame["realized_salvage_m3"] > 0.0)
    frame["incidence_joint_cost"] = (
        frame["realized_subsidy"]
        - frame["burned_stumpage"]
        - frame["agent_gain_vs_zero"]
    )
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
        "agent_A_undiscounted",
        "joint_J_undiscounted",
        "agent_gain_vs_zero",
        "fiscal_cost_vs_zero",
        "joint_cost_vs_zero",
        "fiscal_cost_per_m3",
        "joint_cost_per_m3",
        "max_cohort_overrun_m3",
    ]
    print(frame[columns].to_string(index=False))
    print(f"\nwrote {args.out} and {summary}")


if __name__ == "__main__":
    main()
