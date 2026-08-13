"""Agent LP tests on synthetic toy instances with known optima.

Every fixture is a hand-built list of ``AgentCohort`` records; no real TSA
data is read. ``_solve`` wraps ``solve_agent`` with all cohorts fully offered
unless the test says otherwise. Margins below use the calibrated ``data.py``
constants (planning/economics-calibration.md): green margin = green_price -
45 (harvest) - 30 (transport) - 15 (stumpage); salvage margin = burned_price
- 56 (harvest) - 38 (transport) - 0.25 (stumpage) + 3 (subsidy).
"""

import hashlib

import highspy
import pandas as pd
import pytest

from fresh_salvage import agent, data, fire
from fresh_salvage.agent import AgentCohort

TOLERANCE = 1e-6
BALANCE_TOLERANCE = 1e-9


def _cohort(
    cohort_id: str,
    *,
    standing_volume_m3: float = 1.0,
    burn_rate: float = 0.0,
    green_price_m3: float = 200.0,
    burned_price_m3: float = 130.0,
) -> AgentCohort:
    return AgentCohort(
        cohort_id=cohort_id,
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        standing_volume_m3=standing_volume_m3,
        burn_rate=burn_rate,
        green_price_m3=green_price_m3,
        burned_price_m3=burned_price_m3,
    )


def _solve(cohorts, *, horizon=3, decay_rate=0.85, discount_rate=0.03, offers=None, economics=None):
    if offers is None:
        offers = agent.resolve_offers(cohorts, horizon=horizon)
    return agent.solve_agent(
        cohorts,
        offers,
        horizon=horizon,
        decay_rate=decay_rate,
        discount_rate=discount_rate,
        economics=economics,
        run_id="toy",
    )


def _actions(result):
    return {
        (decision.cohort_id, decision.year): (
            decision.harvest_fraction,
            decision.salvage_fraction,
        )
        for decision in result.decisions
    }


def test_calibrated_margin_decomposition_spf_basis() -> None:
    """Pin the calibrated margin decomposition (planning/economics-calibration.md).

    Three SPF price bases, all derived from the ``data.py`` constants:

    - sawlog basis (a burned sawlog sold as a sawlog): burned price
      127 x 0.65 = 82.55 $/m3, salvage margin 82.55 - 56 - 38 - 0.25 =
      -11.70 $/m3;
    - transition mix (the expected realized price of a green sawlog after
      burn degradation through ``BURNED_GRADE_TRANSITION``; the transition
      is downgrade-only — the burned sawlog remainder drops to pulp, never
      up to peel): 0.65 x (0.00 x 146 + 0.80 x 127 + 0.20 x 55) = 73.19
      $/m3, margin 73.19 - 56 - 38 - 0.25 = -21.06 $/m3 — the headline
      ~-21 $/m3 prompt-salvage basis;
    - development-type mix (the agent LP's actual cohort price: the SPF
      grade split pushed through the full transition matrix): destination
      shares peel 0.092 x 0.55 + 0.805 x 0.00 = 0.0506, saw
      0.092 x 0.35 + 0.805 x 0.80 = 0.6762, pulp 0.103 + 0.092 x 0.10
      + 0.805 x 0.20 = 0.2732, price 0.65 x (0.0506 x 146 + 0.6762 x 127
      + 0.2732 x 55) = 70.39 $/m3, margin -23.86 $/m3.

    Fire-killed wood is a net cost to recover without the subsidy on every
    basis — the behavioral property the recalibration was required to
    produce — but the gap is now moderate (low-to-mid twenties on the prompt
    sawlog stream), so the minimum-subsidy question stays genuinely open.
    """

    green_margin = (
        data.GREEN_PRICES["SPF_Sawlog"]
        - data.GREEN_HARVEST_COST
        - data.TRANSPORT_COST_PER_M3
        - data.GREEN_STUMPAGE_RATE
    )
    assert green_margin == pytest.approx(37.0)

    burned_costs = (
        data.BURNED_HARVEST_COST
        + data.BURNED_TRANSPORT_COST_PER_M3
        + data.BURNED_STUMPAGE_RATE
    )
    assert burned_costs == pytest.approx(94.25)

    # Sawlog basis: a burned sawlog sold as a sawlog.
    burned_price = data.GREEN_PRICES["SPF_Sawlog"] * data.BURNED_PRICE_DISCOUNT
    assert burned_price == pytest.approx(82.55)
    assert burned_price - burned_costs == pytest.approx(-11.70)

    # Transition mix: expected realized price of a green sawlog after burn
    # degradation (the ~-21 $/m3 prompt-salvage headline).
    transition = data.BURNED_GRADE_TRANSITION["Sawlog"]
    transition_price = data.BURNED_PRICE_DISCOUNT * (
        transition["Peeler"] * data.GREEN_PRICES["SPF_Peelers"]
        + transition["Sawlog"] * data.GREEN_PRICES["SPF_Sawlog"]
        + transition["Pulpwood"] * data.GREEN_PRICES["SPF_Pulpwood"]
    )
    assert transition_price == pytest.approx(73.19)
    assert transition_price - burned_costs == pytest.approx(-21.06)

    # Development-type mix: the agent LP's actual volume-weighted SPF price
    # (the species grade split pushed through the full transition matrix).
    splits = data.SPECIES_GRADE_SPLIT["SPF"]
    destination_share = {
        grade_out: sum(
            splits[grade_in] * data.BURNED_GRADE_TRANSITION[grade_in][grade_out]
            for grade_in in splits
        )
        for grade_out in ("Peeler", "Sawlog", "Pulpwood")
    }
    assert destination_share["Peeler"] == pytest.approx(0.0506)
    assert destination_share["Sawlog"] == pytest.approx(0.6762)
    assert destination_share["Pulpwood"] == pytest.approx(0.2732)
    dt_price = data.BURNED_PRICE_DISCOUNT * sum(
        destination_share[grade_out]
        * data.GREEN_PRICES[f"SPF_{data.GRADE_COLUMN_SUFFIX[grade_out]}"]
        for grade_out in destination_share
    )
    assert dt_price == pytest.approx(70.38915)
    assert dt_price - burned_costs == pytest.approx(-23.86085)


def test_unsubsidized_salvage_is_not_economic_at_calibrated_costs() -> None:
    """At subsidy 0 the agent never salvages on the SPF sawlog basis.

    The cohort cannot be green-harvested profitably (green price 0), so fire
    influx accumulates every year; with the salvage margin at -11.7 $/m3 the
    agent still leaves all of it to decay.
    """

    from fresh_salvage.models import Economics

    cohorts = [
        _cohort(
            "c1",
            standing_volume_m3=1.0,
            burn_rate=0.5,
            green_price_m3=0.0,
            burned_price_m3=82.55,
        )
    ]

    result = _solve(cohorts, horizon=2, economics=Economics(subsidy_rate_per_m3=0.0))

    assert result.active_cohort_years == 0
    assert result.objective_value == pytest.approx(0.0)
    for volumes in result.per_year_volumes:
        assert volumes.salvage_volume_m3 == pytest.approx(0.0, abs=BALANCE_TOLERANCE)


def test_subsidy_above_the_margin_gap_flips_salvage_on() -> None:
    """A 25 $/m3 subsidy turns the -11.7 $/m3 margin into +13.3 $/m3.

    The same cohort as above now salvages each year's fire influx
    immediately (0.5 of standing volume in year 1, 0.25 in year 2).
    """

    from fresh_salvage.models import Economics

    cohorts = [
        _cohort(
            "c1",
            standing_volume_m3=1.0,
            burn_rate=0.5,
            green_price_m3=0.0,
            burned_price_m3=82.55,
        )
    ]

    result = _solve(cohorts, horizon=2, economics=Economics(subsidy_rate_per_m3=25.0))
    actions = _actions(result)

    margin = 82.55 - 56.0 - 38.0 - 0.25 + 25.0
    assert margin == pytest.approx(13.3)
    assert actions[("c1", 1)][1] == pytest.approx(0.5)
    assert actions[("c1", 2)][1] == pytest.approx(0.25)
    assert result.objective_value == pytest.approx(
        margin * (0.5 / 1.03 + 0.25 / 1.03**2)
    )


def test_full_offer_harvests_everything_in_year_one_without_fire() -> None:
    cohorts = [_cohort("c1", standing_volume_m3=100.0, burn_rate=0.0)]

    result = _solve(cohorts, horizon=3)
    actions = _actions(result)

    assert result.status == "optimal"
    assert actions[("c1", 1)] == (pytest.approx(1.0), pytest.approx(0.0, abs=TOLERANCE))
    # Green margin is 200 - 45 - 30 - 15 = 110 $/m3, discounted by 1.03 ** 1.
    assert result.objective_value == pytest.approx(100.0 * 110.0 / 1.03)
    assert result.active_cohort_years == 1


def test_discounting_prefers_the_earliest_harvest_year() -> None:
    cohorts = [_cohort("c1", standing_volume_m3=100.0, burn_rate=0.0)]

    result = _solve(cohorts, horizon=5)

    for year in range(2, 6):
        assert result.decisions[year - 1].harvest_fraction == pytest.approx(
            0.0, abs=TOLERANCE
        )
    assert result.decisions[0].harvest_fraction == pytest.approx(1.0)


def test_salvage_is_limited_to_the_on_hand_burned_inventory() -> None:
    # Green margin negative (50 - 90), salvage margin positive (130 - 91.25):
    # the agent waits for fire and salvages each year's influx immediately.
    cohorts = [
        _cohort("c1", standing_volume_m3=1.0, burn_rate=0.5, green_price_m3=50.0)
    ]

    result = _solve(cohorts, horizon=2)
    actions = _actions(result)

    assert actions[("c1", 1)][1] == pytest.approx(0.5)  # the year-1 influx binds
    assert actions[("c1", 2)][1] == pytest.approx(0.25)  # 0.5 * live remainder 0.5
    margin = 130.0 - 56.0 - 38.0 - 0.25 + 3.0
    expected = margin * (0.5 / 1.03 + 0.25 / 1.03**2)
    assert result.objective_value == pytest.approx(expected)
    # Salvage empties the burned inventory every year.
    for volumes in result.per_year_volumes:
        assert volumes.burned_volume_m3 == pytest.approx(0.0, abs=BALANCE_TOLERANCE)


def test_harvest_removes_volume_from_that_years_burn_exposure() -> None:
    cohorts = [
        _cohort("c1", standing_volume_m3=1.0, burn_rate=0.5, green_price_m3=200.0)
    ]

    result = _solve(cohorts, horizon=2)

    # Green margin (110) beats the salvage margin (38.75), so the whole cohort
    # is harvested in year 1 and nothing is left exposed to the year-1 fire.
    assert result.decisions[0].harvest_fraction == pytest.approx(1.0)
    assert result.per_year_volumes[0].burn_influx_m3 == pytest.approx(
        0.0, abs=BALANCE_TOLERANCE
    )
    # Contrast with the no-harvest fire trajectory: influx would be R * 1.
    states = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=0.5,
        harvest_schedule=[0.0, 0.0],
        salvage_schedule=[0.0, 0.0],
    )
    assert states[0].burn_influx == pytest.approx(0.5)


def test_no_double_sell_binds_when_everything_is_sold() -> None:
    # R = 1 burns the entire post-harvest remainder in year 1; with both
    # margins positive the agent sells the whole cohort exactly once.
    cohorts = [
        _cohort("c1", standing_volume_m3=1.0, burn_rate=1.0, green_price_m3=200.0)
    ]

    result = _solve(cohorts, horizon=2)
    actions = _actions(result)

    total = sum(actions[("c1", year)][0] + actions[("c1", year)][1] for year in (1, 2))
    assert total == pytest.approx(1.0, abs=BALANCE_TOLERANCE)


def test_offered_fraction_caps_the_harvest() -> None:
    cohorts = [_cohort("c1", standing_volume_m3=100.0, burn_rate=0.0)]
    offers = [(0.4, 0.0, 0.0)]

    result = _solve(cohorts, horizon=3, offers=offers)

    assert result.decisions[0].harvest_fraction == pytest.approx(0.4)
    assert result.objective_value == pytest.approx(0.4 * 100.0 * 110.0 / 1.03)
    assert result.active_cohort_years == 1


def test_inventory_balances_and_conservation_to_solver_precision() -> None:
    cohorts = [
        _cohort("c1", standing_volume_m3=1.0, burn_rate=0.3),
        _cohort("c2", standing_volume_m3=1.0, burn_rate=0.1, green_price_m3=50.0),
    ]

    result = _solve(cohorts, horizon=4)

    live_before = 2.0  # total standing volume of both cohorts
    burned_before = 0.0
    for volumes in result.per_year_volumes:
        live_after = live_before - volumes.harvest_volume_m3 - volumes.burn_influx_m3
        burned_after = (
            burned_before + volumes.burn_influx_m3 - volumes.salvage_volume_m3
        ) * 0.85
        assert volumes.live_volume_m3 == pytest.approx(
            live_after, abs=BALANCE_TOLERANCE
        )
        assert volumes.burned_volume_m3 == pytest.approx(
            burned_after, abs=BALANCE_TOLERANCE
        )
        live_before, burned_before = live_after, burned_after

    # Conservation: live + burned + harvested + salvaged + decayed == initial.
    decayed = sum(
        (1.0 - 0.85) / 0.85 * volumes.burned_volume_m3
        for volumes in result.per_year_volumes
    )
    accounted = (
        result.per_year_volumes[-1].live_volume_m3
        + result.per_year_volumes[-1].burned_volume_m3
        + sum(v.harvest_volume_m3 for v in result.per_year_volumes)
        + sum(v.salvage_volume_m3 for v in result.per_year_volumes)
        + decayed
    )
    assert accounted == pytest.approx(2.0, abs=BALANCE_TOLERANCE)


def test_unsalvaged_burned_volume_decays_at_point_eight_five_per_year() -> None:
    # Negative margins on both channels: nothing is harvested or salvaged.
    cohorts = [
        _cohort(
            "idle",
            standing_volume_m3=1.0,
            burn_rate=0.2,
            green_price_m3=50.0,
            burned_price_m3=30.0,
        )
    ]

    result = _solve(cohorts, horizon=3)

    assert result.active_cohort_years == 0
    # B[1] = 0.85 * 0.2; B[2] = 0.85 * (B[1] + 0.2 * 0.8); ...
    assert result.per_year_volumes[0].burned_volume_m3 == pytest.approx(0.85 * 0.2)
    assert result.per_year_volumes[1].burned_volume_m3 == pytest.approx(
        0.85 * (0.85 * 0.2 + 0.2 * 0.8)
    )
    assert result.per_year_volumes[2].burned_volume_m3 == pytest.approx(
        0.85 * (0.85 * (0.85 * 0.2 + 0.2 * 0.8) + 0.2 * 0.8 * 0.8)
    )


def test_lp_trajectory_matches_fire_simulation() -> None:
    cohorts = [_cohort("c1", standing_volume_m3=80.0, burn_rate=0.25)]

    result = _solve(cohorts, horizon=3)

    states = fire.simulate_cohort_years(
        initial_live=80.0,
        burn_rate=0.25,
        harvest_schedule=[d.harvest_volume_m3 for d in result.decisions],
        salvage_schedule=[d.salvage_volume_m3 for d in result.decisions],
    )
    for state, volumes in zip(states, result.per_year_volumes, strict=True):
        assert volumes.burn_influx_m3 == pytest.approx(
            state.burn_influx, abs=BALANCE_TOLERANCE
        )
        assert volumes.live_volume_m3 == pytest.approx(
            state.live_after, abs=BALANCE_TOLERANCE
        )
        assert volumes.burned_volume_m3 == pytest.approx(
            state.burned_after, abs=BALANCE_TOLERANCE
        )


def test_lp_has_no_integer_variables() -> None:
    cohorts = [
        _cohort("c1", burn_rate=0.1),
        _cohort("c2", burn_rate=0.02),
    ]

    built = agent.build_agent_lp(
        cohorts, agent.resolve_offers(cohorts, horizon=3), horizon=3
    )
    integrality = list(built.model.getLp().integrality_)

    # A pure LP carries either no integrality vector at all or only
    # continuous entries.
    assert all(entry == highspy.HighsIntegrality.kContinuous for entry in integrality)
    assert built.model.getNumCol() == 4 * len(cohorts) * 3
    assert built.model.getNumRow() == len(cohorts) * (3 * 3 + 1)


def test_solve_is_deterministic_across_runs() -> None:
    cohorts = [
        _cohort("c1", standing_volume_m3=100.0, burn_rate=0.1),
        _cohort("c2", standing_volume_m3=250.0, burn_rate=0.02, green_price_m3=170.0),
    ]

    first = _solve(cohorts, horizon=5)
    second = _solve(cohorts, horizon=5)

    assert first.objective_value == second.objective_value
    assert _actions(first) == _actions(second)


def test_decision_table_emits_every_cohort_year_including_zeros() -> None:
    cohorts = [
        _cohort("c1", green_price_m3=10.0, burned_price_m3=10.0),
        _cohort("c2", green_price_m3=10.0, burned_price_m3=10.0),
    ]

    result = _solve(cohorts, horizon=4)

    assert len(result.decisions) == 2 * 4
    assert result.active_cohort_years == 0
    assert all(
        decision.harvest_fraction == 0.0 and decision.salvage_fraction == 0.0
        for decision in result.decisions
    )


def test_build_fails_fast_on_invalid_inputs() -> None:
    cohorts = [_cohort("c1")]
    offers = agent.resolve_offers(cohorts, horizon=3)
    with pytest.raises(agent.AgentError, match="at least one cohort"):
        agent.build_agent_lp([], [], horizon=3)
    with pytest.raises(agent.AgentError, match="horizon"):
        agent.build_agent_lp(cohorts, offers, horizon=0)
    with pytest.raises(agent.AgentError, match="offers cover"):
        agent.build_agent_lp(cohorts, [], horizon=3)
    with pytest.raises(agent.AgentError, match="horizon of"):
        agent.build_agent_lp(cohorts, [(1.0, 1.0)], horizon=3)
    with pytest.raises(agent.AgentError, match="decay_rate"):
        agent.build_agent_lp(cohorts, offers, horizon=3, decay_rate=1.5)
    with pytest.raises(agent.AgentError, match="discount_rate"):
        agent.build_agent_lp(cohorts, offers, horizon=3, discount_rate=-0.1)


def test_resolve_offers_defaults_to_full_offer() -> None:
    cohorts = [_cohort("c1"), _cohort("c2")]

    offers = agent.resolve_offers(cohorts, horizon=2)

    assert offers == [(1.0, 1.0), (1.0, 1.0)]


def test_resolve_offers_fails_fast_on_invalid_parameters() -> None:
    cohorts = [_cohort("c1")]
    with pytest.raises(agent.AgentError, match="horizon"):
        agent.resolve_offers(cohorts, horizon=0)
    with pytest.raises(agent.AgentError, match="default_offer_fraction"):
        agent.resolve_offers(cohorts, horizon=1, default_offer_fraction=1.5)


def test_resolve_offers_reads_a_principal_offer_table(tmp_path) -> None:
    cohorts = [_cohort("c1"), _cohort("c2")]
    offers_path = tmp_path / "offers.parquet"
    pd.DataFrame(
        [
            {"cohort_id": "c1", "year": 1, "offer_fraction": 0.25},
            {"cohort_id": "c1", "year": 2, "offer_fraction": 0.75},
            {"cohort_id": "c2", "year": 2, "offer_fraction": 0.5},
        ]
    ).to_parquet(offers_path, index=False)

    offers = agent.resolve_offers(cohorts, horizon=3, offers_path=offers_path)

    assert offers == [(0.25, 0.75, 0.0), (0.0, 0.5, 0.0)]


def test_resolve_offers_fails_fast_on_bad_offer_tables(tmp_path) -> None:
    cohorts = [_cohort("c1")]

    missing_columns = tmp_path / "missing_columns.csv"
    pd.DataFrame([{"cohort_id": "c1", "offer_fraction": 0.5}]).to_csv(
        missing_columns, index=False
    )
    with pytest.raises(agent.AgentError, match="missing required columns"):
        agent.resolve_offers(cohorts, horizon=1, offers_path=missing_columns)

    duplicates = tmp_path / "duplicates.csv"
    pd.DataFrame(
        [
            {"cohort_id": "c1", "year": 1, "offer_fraction": 0.5},
            {"cohort_id": "c1", "year": 1, "offer_fraction": 0.6},
        ]
    ).to_csv(duplicates, index=False)
    with pytest.raises(agent.AgentError, match="duplicate"):
        agent.resolve_offers(cohorts, horizon=1, offers_path=duplicates)

    out_of_bounds = tmp_path / "out_of_bounds.csv"
    pd.DataFrame([{"cohort_id": "c1", "year": 1, "offer_fraction": 1.5}]).to_csv(
        out_of_bounds, index=False
    )
    with pytest.raises(agent.AgentError, match="outside"):
        agent.resolve_offers(cohorts, horizon=1, offers_path=out_of_bounds)

    unknown = tmp_path / "unknown.csv"
    pd.DataFrame([{"cohort_id": "ghost", "year": 1, "offer_fraction": 0.5}]).to_csv(
        unknown, index=False
    )
    with pytest.raises(agent.AgentError, match="absent from the ARE inputs"):
        agent.resolve_offers(cohorts, horizon=1, offers_path=unknown)

    with pytest.raises(agent.AgentError, match="not found"):
        agent.resolve_offers(
            cohorts, horizon=1, offers_path=tmp_path / "absent.parquet"
        )


def test_parse_action_fraction_fails_fast_beyond_tolerance() -> None:
    assert agent._parse_action_fraction(1.0 + 1e-12, 0) == 1.0
    assert agent._parse_action_fraction(-1e-12, 0) == 0.0
    with pytest.raises(agent.AgentError) as excinfo:
        agent._parse_action_fraction(1.0 + 1e-6, 7)
    assert excinfo.value.code == "agent_fraction_out_of_bounds"


def _write_toy_run_config(tmp_path, *, offers_path=None):
    """Write a tiny synthetic stands/ARE/yields triple and its config."""

    from fresh_salvage.models import AgentRunConfig

    stands_path = tmp_path / "stands.parquet"
    pd.DataFrame(
        [
            {
                "development_type": "SPF_SBPS",
                "Total_Green_Vol": 1_000.0,
                "Total_Burned_Vol": 200.0,
                **{column: 0.0 for column in data.GRADE_COLUMNS},
                "SPF_Peelers_Vol": 92.0,
                "SPF_Sawlog_Vol": 805.0,
                "SPF_Pulpwood_Vol": 103.0,
                **{column: 0.0 for column in data.BURNED_GRADE_COLUMNS},
                "B_SPF_Sawlog_Vol": 200.0,
            }
        ]
    ).to_parquet(stands_path, index=False)

    are_path = tmp_path / "toy.are"
    are_path.write_text(
        "toy ARE section\n*A 29 1 7 sbps_pli 101 45 120.5\n",
        encoding="utf-8",
    )

    yields_path = tmp_path / "yields.csv"
    pd.DataFrame(
        [
            {"curve_id": 101, "age": 0, "volume": 0.0},
            {"curve_id": 101, "age": 50, "volume": 250.0},
            {"curve_id": 101, "age": 100, "volume": 400.0},
        ]
    ).to_csv(yields_path, index=False)

    return AgentRunConfig(
        run_id="toy-agent-e2e",
        stands_path=stands_path,
        are_path=are_path,
        yields_path=yields_path,
        horizon=3,
        offers_path=offers_path,
        output_root=tmp_path / "out",
    )


def test_load_cohorts_parses_toy_inputs(tmp_path) -> None:
    config = _write_toy_run_config(tmp_path)

    cohorts = agent.load_cohorts(config)

    assert len(cohorts) == 1
    cohort = cohorts[0]
    assert cohort.cohort_id == "1:7:sbps_pli:101:45"
    assert cohort.development_type == "SPF_SBPS"
    assert cohort.burn_rate == pytest.approx(0.01)
    assert cohort.standing_volume_m3 == pytest.approx(120.5 * 225.0)
    assert cohort.green_price_m3 == pytest.approx(
        (805.0 * 127.0 + 92.0 * 146.0 + 103.0 * 55.0) / 1_000.0
    )
    assert cohort.burned_price_m3 == pytest.approx(127.0 * 0.65)


def test_load_cohorts_fails_fast_on_missing_inputs(tmp_path) -> None:
    from fresh_salvage.models import AgentRunConfig

    config = AgentRunConfig(
        stands_path=tmp_path / "missing.parquet",
        are_path=tmp_path / "missing.are",
        yields_path=tmp_path / "missing.csv",
        output_root=tmp_path / "out",
    )

    with pytest.raises(agent.AgentError, match="not found"):
        agent.load_cohorts(config)


def test_run_agent_end_to_end_writes_artifacts_and_manifest(tmp_path) -> None:
    from fresh_salvage.models import AgentManifest

    config = _write_toy_run_config(tmp_path)

    result = agent.run_agent(config)

    assert result.status == "optimal"
    assert result.cohorts == 1
    assert result.data_path.is_file()
    assert result.csv_path.is_file()
    assert result.manifest_path.is_file()

    decisions = pd.read_parquet(result.data_path)
    assert len(decisions) == 3  # one cohort x three years, zeros included

    manifest = AgentManifest.read_json(result.manifest_path)
    expected_checksums = {
        "stands": hashlib.sha256(config.stands_path.read_bytes()).hexdigest(),
        "are": hashlib.sha256(config.are_path.read_bytes()).hexdigest(),
        "yields": hashlib.sha256(config.yields_path.read_bytes()).hexdigest(),
    }
    assert manifest.source_sha256 == expected_checksums
    assert manifest.status == "optimal"
    assert manifest.run_id == "toy-agent-e2e"


def test_run_agent_records_offers_checksum_when_offered(tmp_path) -> None:
    from fresh_salvage.models import AgentManifest

    cohorts_config = _write_toy_run_config(tmp_path)
    offers_path = tmp_path / "offers.csv"
    cohort_id = "1:7:sbps_pli:101:45"
    pd.DataFrame(
        [
            {"cohort_id": cohort_id, "year": year, "offer_fraction": 0.5}
            for year in (1, 2, 3)
        ]
    ).to_csv(offers_path, index=False)
    config = cohorts_config.model_copy(update={"offers_path": offers_path})

    result = agent.run_agent(config)

    manifest = AgentManifest.read_json(result.manifest_path)
    assert manifest.offers_path == offers_path
    assert manifest.source_sha256["offers"] == hashlib.sha256(
        offers_path.read_bytes()
    ).hexdigest()
    assert all(
        decision.harvest_fraction <= 0.5 + TOLERANCE
        and decision.salvage_fraction <= 0.5 + TOLERANCE
        for decision in result.decisions
    )
