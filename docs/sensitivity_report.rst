Sensitivity Report
==================

``fresh-salvage sensitivity-run`` writes a Markdown report that summarizes a
one-at-a-time rolling-horizon sensitivity analysis. For each configured
parameter, the driver changes only that parameter and preserves all other
``base`` values. It automatically includes the base value when it is absent
from the parameter's configured value list.

Running The Report
------------------

Update the external ``stands_path``, ``yields_path``, and ``bridge_path``
locations in ``examples/sensitivity_tsa29.yaml`` before running it. Then run:

.. code-block:: bash

   fresh-salvage sensitivity-run examples/sensitivity_tsa29.yaml --json --strict

The report is written to
``outputs/sensitivity_tsa29/reports/tsa29-sensitivity-results.md``. The
``--strict`` flag returns a non-zero exit status if any ordinary sensitivity
scenario fails. Do not interpret a partial report as a complete comparison.

Report Contents
---------------

``Analysis Summary`` records the number of configured parameters and ordinary
sensitivity scenarios, their success status, and wall-clock time. ``Effective
Baseline Settings`` is the resolved control configuration used for every
one-at-a-time comparison.

``Scenarios Examined`` lists every parameter value, whether it is the
baseline, and any scenario failure code. Each ``Results: <parameter>`` section
then contains:

- ``Outcome By Setting``: green harvest, burned salvage, burned area,
  principal objective, and agent objective at every tested setting.
- ``Change From Baseline``: the signed difference from the baseline scenario;
  a positive value means the metric increased.
- ``Response Summary``: baseline, minimum, maximum, range, and observed
  direction for each recorded metric.

Subsidy Flip Points
-------------------

The TSA29 example's ``flip_point_search`` block additionally requests an
adaptive search for the subsidy at which burned salvage becomes positive. A
separate search is run for every configured value of each non-subsidy
parameter. The report's ``Subsidy Flip Points`` table has one row per search:

- ``Inactive lower`` is the highest observed subsidy with burned salvage less
  than or equal to the configured threshold.
- ``Active upper`` is the lowest observed subsidy with burned salvage greater
  than the threshold.
- ``Width`` is the uncertainty interval between those two tested subsidies.
- ``Probes`` counts midpoint runs, excluding the two endpoint checks.
- ``Converged`` is ``yes`` when the bracket meets the configured tolerance.

The table omits ``subsidy_rate_per_m3`` because it is the value being searched,
not a fixed condition that can move its own flip point. See
:doc:`flip_point_search` for the binary-search algorithm and bracketing rules.
``not_bracketed`` means the configured interval did not contain a transition.
For example, the fire-free ``burn_rate_multiplier: 0.0`` control has no burned
wood to salvage, so its upper subsidy endpoint remains inactive. This is an
expected sensitivity finding, not a failed ordinary sensitivity scenario.

Operation-Cost Sensitivity
--------------------------

The TSA29 example includes ``burned_harvest_cost: [56.0, 67.2, 78.4]``. These
values represent the calibrated prompt-salvage operating cost and 20% and 40%
increases, in $/m3. This is the marginal operating cost for harvesting burned
wood, so it directly changes the subsidy needed to activate salvage. Compare
the corresponding ``burned_harvest_cost`` rows in ``Subsidy Flip Points``:
a higher active-upper bound means more subsidy is needed to make salvage
privately viable.

This is intentionally separate from green-harvest and transport costs. Add
``green_harvest_cost``, ``green_transport_cost_per_m3``, or
``burned_transport_cost_per_m3`` to ``parameters`` when those distinct cost
mechanisms are the question; each will receive its own one-at-a-time outcome
and flip-point rows.

Evidence Files
--------------

The report is a readable summary. Use its evidence files for analysis or audit:

- ``data/tsa29-sensitivity-observations.jsonl`` has the ordinary
  scenario-level metrics and rolling-horizon manifest locations.
- ``data/tsa29-sensitivity-comparisons.csv`` has the response summaries.
- ``data/tsa29-sensitivity-flip-points.csv`` has the flip-point brackets,
  convergence flags, and flip-search manifest locations.
- ``manifests/tsa29-sensitivity-sensitivity-manifest.json`` records the
  effective configuration and paths to all report artifacts.

Every flip-point row can require two endpoint rolling-horizon runs plus up to
the configured midpoint limit. The flip-point section therefore adds material
runtime beyond the ordinary sensitivity scenarios.
