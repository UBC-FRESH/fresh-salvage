"""P8.2/P8.3 equivalence: rolling-horizon usage must reduce exactly.

The rolling-horizon engine builds principal/agent LPs with a horizon of at
most one ``period_length``, no decadal growth surface, the LP-year grade
index, and no burned-stock reset. The Phase 8 switches must leave that LP
bit-identical (arrays and solution) to the branch-base module at ``53ba8d1``
(content-identical to ``09ed633``).

The base module is extracted with ``git show``. A shallow CI checkout does
not carry the base commit, so the cross-module tests skip there; the
in-module reduction tests (defaults versus reducing arguments) always run.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import types
from pathlib import Path

import pytest

from fresh_salvage import agent, principal

BASE_COMMIT = "53ba8d1"
REPO_ROOT = Path(__file__).resolve().parents[1]
HORIZON = 10
PERIOD_LENGTH = 10

_BASE_MODULES: dict[str, types.ModuleType] = {}


def _base_module(relative_path: str) -> types.ModuleType:
    """Load one source module from the branch base commit (or skip)."""

    if relative_path not in _BASE_MODULES:
        shown = subprocess.run(
            ["git", "show", f"{BASE_COMMIT}:{relative_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if shown.returncode != 0:
            pytest.skip(
                f"{BASE_COMMIT}:{relative_path} is unavailable in this checkout "
                "(shallow clone); the cross-module comparison needs full history"
            )
        module = types.ModuleType(f"fresh_salvage_base_{Path(relative_path).stem}")
        module.__file__ = str(REPO_ROOT / relative_path)
        # Register before exec: the module's ``from __future__ import
        # annotations`` makes @dataclass resolve annotations through
        # ``sys.modules[cls.__module__]``.
        sys.modules[module.__name__] = module
        exec(compile(shown.stdout, module.__file__, "exec"), module.__dict__)
        _BASE_MODULES[relative_path] = module
    return _BASE_MODULES[relative_path]


def _lp_arrays(built) -> dict[str, list[float]]:
    """Every array that defines a built HiGHS LP, for exact comparison."""

    lp = built.model.getLp()
    return {
        "col_cost": list(lp.col_cost_),
        "col_lower": list(lp.col_lower_),
        "col_upper": list(lp.col_upper_),
        "row_lower": list(lp.row_lower_),
        "row_upper": list(lp.row_upper_),
        "a_start": list(lp.a_matrix_.start_),
        "a_index": list(lp.a_matrix_.index_),
        "a_value": list(lp.a_matrix_.value_),
        "offset": [lp.offset_],
    }


def _principal_cohorts(*, growth: bool) -> list[principal.PrincipalCohort]:
    """Two cohorts on the calibrated economic surface (toy volumes)."""

    cohorts = [
        principal.PrincipalCohort(
            cohort_id="a",
            stratum_code="sbps_pli",
            development_type="SPF_SBPS",
            area_ha=10.0,
            green_volume_m3=1200.0,
            burned_volume_m3=120.0,
            cashflow=4_800.0,
            burned_value=1_200.0,
            burn_rate=0.01,
            burned_grade_mix=(0.2, 0.6, 0.2, 0.0),
            burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
            green_stumpage_rate=4.0,
            burned_stumpage_rate=0.25,
            subsidy_rate_per_m3=3.0,
        ),
        principal.PrincipalCohort(
            cohort_id="b",
            stratum_code="idf_fd",
            development_type="SPF_IDF",
            area_ha=5.0,
            green_volume_m3=2500.0,
            burned_volume_m3=250.0,
            cashflow=10_000.0,
            burned_value=2_500.0,
            burn_rate=0.005,
            green_stumpage_rate=4.0,
            burned_stumpage_rate=0.25,
            subsidy_rate_per_m3=3.0,
        ),
    ]
    if not growth:
        return cohorts
    # green_volume_m3 / area_ha is exact for these toy volumes, so
    # area_ha * v_0 reproduces the parsed t=0 volume bit-exactly.
    return [
        dataclasses.replace(
            cohort, decadal_volume_scale=(cohort.green_volume_m3 / cohort.area_ha,)
        )
        for cohort in cohorts
    ]


def _agent_cohorts(*, growth: bool) -> list[agent.AgentCohort]:
    """Two cohorts with fire dynamics and graded burned pricing."""

    cohorts = [
        agent.AgentCohort(
            cohort_id="a",
            stratum_code="sbps_pli",
            development_type="SPF_SBPS",
            area_ha=10.0,
            standing_volume_m3=1200.0,
            burn_rate=0.02,
            green_price_m3=200.0,
            burned_price_m3=130.0,
            burned_grade_mix=(0.2, 0.6, 0.2, 0.0),
            burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        ),
        agent.AgentCohort(
            cohort_id="b",
            stratum_code="idf_fd",
            development_type="SPF_IDF",
            area_ha=5.0,
            standing_volume_m3=2500.0,
            burn_rate=0.005,
            green_price_m3=150.0,
            burned_price_m3=100.0,
        ),
    ]
    if not growth:
        return cohorts
    return [
        dataclasses.replace(
            cohort, decadal_volume_scale=(cohort.standing_volume_m3 / cohort.area_ha,)
        )
        for cohort in cohorts
    ]


def _reduced_principal_lp():
    return principal.build_principal_lp(
        _principal_cohorts(growth=True),
        horizon=HORIZON,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[400.0, 0.0],
        period_length=PERIOD_LENGTH,
        grade_clock_period=PERIOD_LENGTH,
        burned_stock_reset=True,
    )


def _static_principal_lp():
    return principal.build_principal_lp(
        _principal_cohorts(growth=False),
        horizon=HORIZON,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[400.0, 0.0],
    )


def _reduced_agent_lp():
    cohorts = _agent_cohorts(growth=True)
    return agent.build_agent_lp(
        cohorts,
        agent.resolve_offers(cohorts, horizon=HORIZON),
        horizon=HORIZON,
        period_length=PERIOD_LENGTH,
        grade_clock_period=PERIOD_LENGTH,
        burned_stock_reset=True,
    )


def _static_agent_lp():
    cohorts = _agent_cohorts(growth=False)
    return agent.build_agent_lp(
        cohorts,
        agent.resolve_offers(cohorts, horizon=HORIZON),
        horizon=HORIZON,
    )


def test_principal_horizon_within_one_decade_reduces_bit_exactly() -> None:
    assert _lp_arrays(_reduced_principal_lp()) == _lp_arrays(_static_principal_lp())


def test_agent_horizon_within_one_decade_reduces_bit_exactly() -> None:
    assert _lp_arrays(_reduced_agent_lp()) == _lp_arrays(_static_agent_lp())


def test_principal_defaults_match_branch_base_at_any_horizon() -> None:
    base = _base_module("src/fresh_salvage/principal.py")

    for horizon in (HORIZON, 15):
        base_lp = base.build_principal_lp(
            _principal_cohorts(growth=False), horizon=horizon, aac_annual_m3=5_000.0
        )
        current_lp = principal.build_principal_lp(
            _principal_cohorts(growth=False), horizon=horizon, aac_annual_m3=5_000.0
        )
        assert _lp_arrays(current_lp) == _lp_arrays(base_lp)


def test_principal_horizon_within_one_decade_matches_branch_base() -> None:
    base = _base_module("src/fresh_salvage/principal.py")

    base_lp = base.build_principal_lp(
        _principal_cohorts(growth=False),
        horizon=HORIZON,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[400.0, 0.0],
    )

    assert _lp_arrays(_reduced_principal_lp()) == _lp_arrays(base_lp)


def test_principal_solution_matches_branch_base() -> None:
    base = _base_module("src/fresh_salvage/principal.py")

    base_result = base.solve_principal(
        _principal_cohorts(growth=False),
        horizon=HORIZON,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[400.0, 0.0],
        run_id="equivalence",
    )
    current_result = principal.solve_principal(
        _principal_cohorts(growth=True),
        horizon=HORIZON,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[400.0, 0.0],
        period_length=PERIOD_LENGTH,
        grade_clock_period=PERIOD_LENGTH,
        burned_stock_reset=True,
        run_id="equivalence",
    )

    assert current_result.objective_value == pytest.approx(base_result.objective_value)
    assert [
        (offer.cohort_id, offer.year) for offer in current_result.offers
    ] == [(offer.cohort_id, offer.year) for offer in base_result.offers]
    assert [offer.offer_fraction for offer in current_result.offers] == pytest.approx(
        [offer.offer_fraction for offer in base_result.offers]
    )


def test_agent_defaults_match_branch_base_at_any_horizon() -> None:
    base = _base_module("src/fresh_salvage/agent.py")

    for horizon in (HORIZON, 15):
        base_cohorts = _agent_cohorts(growth=False)
        base_lp = base.build_agent_lp(
            base_cohorts,
            base.resolve_offers(base_cohorts, horizon=horizon),
            horizon=horizon,
        )
        current_cohorts = _agent_cohorts(growth=False)
        current_lp = agent.build_agent_lp(
            current_cohorts,
            agent.resolve_offers(current_cohorts, horizon=horizon),
            horizon=horizon,
        )
        assert _lp_arrays(current_lp) == _lp_arrays(base_lp)


def test_agent_horizon_within_one_decade_matches_branch_base() -> None:
    base = _base_module("src/fresh_salvage/agent.py")

    base_cohorts = _agent_cohorts(growth=False)
    base_lp = base.build_agent_lp(
        base_cohorts,
        base.resolve_offers(base_cohorts, horizon=HORIZON),
        horizon=HORIZON,
    )

    assert _lp_arrays(_reduced_agent_lp()) == _lp_arrays(base_lp)


def test_agent_solution_matches_branch_base() -> None:
    base = _base_module("src/fresh_salvage/agent.py")

    base_cohorts = _agent_cohorts(growth=False)
    base_result = base.solve_agent(
        base_cohorts,
        base.resolve_offers(base_cohorts, horizon=HORIZON),
        horizon=HORIZON,
        run_id="equivalence",
    )
    current_cohorts = _agent_cohorts(growth=True)
    current_result = agent.solve_agent(
        current_cohorts,
        agent.resolve_offers(current_cohorts, horizon=HORIZON),
        horizon=HORIZON,
        period_length=PERIOD_LENGTH,
        grade_clock_period=PERIOD_LENGTH,
        burned_stock_reset=True,
        run_id="equivalence",
    )

    assert current_result.objective_value == pytest.approx(base_result.objective_value)
    assert [
        (decision.cohort_id, decision.year) for decision in current_result.decisions
    ] == [(decision.cohort_id, decision.year) for decision in base_result.decisions]
    assert [
        (decision.harvest_fraction, decision.salvage_fraction)
        for decision in current_result.decisions
    ] == pytest.approx(
        [
            (decision.harvest_fraction, decision.salvage_fraction)
            for decision in base_result.decisions
        ]
    )
