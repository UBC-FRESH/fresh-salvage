Flip-Point Search
=================

The flip-point search locates the lowest subsidy at which the fire-active
rolling-horizon model produces burned-wood salvage. It is an adaptive binary
search, not a fixed scenario grid: every probe runs the complete configured
rolling-horizon projection, and the next probe depends on the previous result.

Search Structure
----------------

``examples/ensemble_flip_sweep.yaml`` searches ``subsidy_rate_per_m3`` between
20.0 and 30.0 $/m3 while holding ``burn_rate_multiplier: 1.0`` fixed. The
driver first runs both endpoints. The lower endpoint must be inactive and the
upper endpoint must be active, otherwise the requested flip is not bracketed
and the command fails.

For each midpoint, the driver reads ``total_burned_harvest_m3`` from the
completed rolling-horizon run:

- A metric value greater than ``threshold`` is active, so the midpoint becomes
  the new upper bound.
- A value less than or equal to ``threshold`` is inactive, so the midpoint
  becomes the new lower bound.
- The next probe is the midpoint of the updated bounds.

The example has ``threshold: 0.0``. Therefore, any positive burned salvage is
active, while zero burned salvage is inactive. It has ``tolerance: 0.5``, so
the search stops after the inactive/active subsidy bracket is no wider than
0.50 $/m3. ``iterations: 8`` is a safety cap on midpoint probes; endpoint
checks do not count against it.

The reported result is a bracket, not an exact break-even value. Its ``lower``
bound is the highest observed inactive subsidy and its ``upper`` bound is the
lowest observed active subsidy. This procedure assumes the chosen metric moves
from inactive to active monotonically across the supplied interval. Inspect the
recorded scenario curve before relying on that assumption for a new model
configuration.

Running The Example
-------------------

Install the package from the repository root if it is not already installed:

.. code-block:: bash

   python -m pip install -e .

Before executing the example, edit its three external input locations to point
to accessible data on the current machine:

- ``base.stands_path``: an ingested stands Parquet file.
- ``base.yields_path``: the validated Woodstock yields CSV.
- ``base.bridge_path``: the validated WS3 bridge directory.

Those TSA29 inputs are deliberately not included in this public repository.
Keep ``base.workers: 1`` and ``max_workers: 1`` as shown. The search is
sequential, and holding the WS3 worker count fixed avoids small changes in WS3
step objectives between probes.

Run the search from the repository root:

.. code-block:: bash

   fresh-salvage ensemble-run examples/ensemble_flip_sweep.yaml --json --strict

``--json`` prints the machine-readable ensemble summary. ``--strict`` makes a
completed ensemble exit non-zero if any scenario fails. Input, bridge, and
binary-bracketing errors are fatal regardless of that flag.

Results
-------

The example writes its artifacts below ``outputs/ensemble_flip_sweep/``. The
ensemble JSON summary and ensemble manifest contain ``binary_search_result``
with the final ``lower``, ``upper``, ``bracket_width``, ``midpoint_probes``,
``tolerance``, and ``converged`` fields. Per-scenario rolling-horizon
manifests provide the corresponding ``total_burned_harvest_m3`` observations
and full input provenance.

If ``converged`` is false, the iteration cap was reached before the requested
tolerance. Increase ``iterations`` or use a larger ``tolerance``. If the lower
endpoint is already active or the upper endpoint remains inactive, widen or
move the ``lower`` and ``upper`` interval so that it brackets the transition.

Sensitivity Flip Points
-----------------------

``examples/sensitivity_tsa29.yaml`` enables the same search through its
``flip_point_search`` block. The sensitivity driver first completes its usual
one-at-a-time outcome sweeps. It then runs one independent subsidy search for
every configured value of every parameter other than ``subsidy_rate_per_m3``;
the baseline is included when it was not already listed. Each search holds that
parameter value and all other base settings fixed. The resulting
``reports/tsa29-sensitivity-results.md`` contains a ``Subsidy Flip Points``
table, and ``data/tsa29-sensitivity-flip-points.csv`` provides the same
brackets in machine-readable form.

The subsidy-rate sensitivity itself is omitted from this table because subsidy
is the searched axis, rather than a condition that can shift its own flip
point. This analysis is substantially more expensive than a sampled
sensitivity sweep: each reported row needs two endpoint rolling-horizon runs
and up to the configured number of midpoint probes.
