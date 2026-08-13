Development
===========

Setup
-----

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

The ``dev`` extra carries pytest, ruff, Sphinx, the RTD theme, build, and
twine. Python 3.11 is the oldest supported version (CI tests 3.11 and
3.12).

External Dependencies
---------------------

``ws3`` is an external FRESH-ecosystem dependency resolved from a source
checkout, not from PyPI. Put the checkout on ``PYTHONPATH`` before running
the WS3-dependent tests or any pipeline command:

.. code-block:: bash

   export PYTHONPATH=/path/to/ws3

The femic bridge writer is imported from the femic source tree, resolved via
the ``FEMIC_SRC`` environment variable with a configured default path as
fallback. Ensemble worker processes are spawned (not forked) and inherit
``PYTHONPATH``.

Local Checks
------------

.. code-block:: bash

   python -m ruff check .
   python -m pytest
   sphinx-build -b html docs _build/html -W
   python -m build
   twine check dist/*

The suite holds 203 tests (201 passed, 2 skipped unless the optional ws3
checkout is on ``PYTHONPATH``; with it, all 203 pass). Ruff is configured
for rule sets ``E``, ``F``,
``I``, ``UP``, and ``W`` at a 100-column line length, targeting Python
3.11. Both checks must stay clean on every commit; docs-only edits should
not affect either, but verify before committing. The Sphinx build runs with
``-W`` (warnings are errors) — keep toctree references, underlines, and
directives exact.

Testing Conventions
-------------------

- **Synthetic, public-safe fixtures only.** Tests never read the real
  WL_VFSL layer or the femic TSA29 bridge; ``test_data.py`` builds synthetic
  stand rows column-by-column, and the ensemble driver tests mock
  ``rh.run_rh`` for driver-logic coverage.
- **External-dependency tests skip cleanly.** The WS3-dependent tests expect
  the ``ws3`` checkout on ``PYTHONPATH``; the one real end-to-end ensemble
  path (2 scenarios x 1 step at horizon 3, process pool included)
  additionally needs the local TSA29 inputs and skips when they are
  unavailable. CI must pass without private data, Gurobi, or network
  downloads beyond package installation.
- **Calibrated numbers are pinned, not recomputed.** The economics
  calibration is locked by explicit tests (for example the SPF margin
  decompositions and the no-salvage-at-0 / full-salvage-at-25 agent
  behaviour tests); changing a ``data.py`` economic constant without
  updating its rationale in ``planning/economics-calibration.md`` will fail
  the suite — that is intentional.
- **Determinism is tested.** Artifact ordering (grid-order scenario records,
  sorted JSON keys), area conservation to 1e-6, and fraction balances are
  asserted, not eyeballed.

Error-Code Conventions
----------------------

Every pipeline layer raises exactly one error class carrying a structured
machine-readable code::

   class PrincipalError(RuntimeError):
       def __init__(self, code: str, message: str) -> None: ...

(``fire.py`` is the exception: it raises ``UnknownBurnRateError`` /
``FireDynamicsError`` ``ValueError`` subclasses, since its helpers are pure
functions consumed by other layers.)

- Codes are snake_case and layer-prefixed: ``data_severity_unmatched``,
  ``ws3_bridge_age_unsmashed``, ``principal_are_unparseable``,
  ``agent_offers_unknown_cohorts``, ``rh_state_duplicate_cohort``,
  ``ensemble_axis_unknown``.
- **Fail fast at the boundary.** Parse external inputs into typed Pydantic
  records and raise on the first defect; core logic never re-validates
  defensively and never silently defaults (an unknown BEC zone has no
  "nearest" fire rate).
- The CLI converts any exception into a ``Diagnostic`` record
  (``severity``/``code``/``message``/``context``), prints it (structured
  JSON under ``--json``), and exits 1.

Manifest And Provenance Pattern
-------------------------------

Every run emits a JSON manifest under ``manifests/`` built from a typed
``*Manifest`` model (``manifest_version: "1.0"``): run id, timestamps,
status, headline metrics, input SHA-256 digests, the full effective config
snapshot (defaults resolved), and the run's diagnostics. Artifact paths live
behind ``ArtifactLayout`` (``data/`` / ``manifests/`` / ``logs/`` under the
configured ``output_root``; file names derived from the run id). When you
add a pipeline output, write it through the layout and record its provenance
in the manifest — a result without provenance is treated as non-evidence in
this project.

Adding A Scenario Knob End-To-End
---------------------------------

The economic surface is the template for adding a new tunable. To add one
cleanly:

1. **Default constant** in ``data.py`` with a comment stating the rationale
   and provenance label (market anchor / derived / assumption).
2. **Config field with validation** on ``Economics`` (for the LP layers) —
   validators fail at config parse time, not mid-pipeline.
3. **Flat field on ``RHRunConfig``** plus assembly in
   ``RHRunConfig.economics()``, so the ensemble driver accepts it as a named
   axis with no driver change.
4. **Ingestion threading** if the knob shapes the stands table (the
   ``ScenarioRunConfig.economics`` section is the precedent).
5. **Manifest echo** so the effective value lands in the run's
   ``parameters``/``config`` block.
6. **Tests**: a parse-time validation test, a manifest-echo test, and a
   behavioural test that pins the knob's effect on the LP outcome.
7. **Docs**: the parameter table in :doc:`model_semantics` and the field
   tables in :doc:`cli`.

Pull Requests And CI
--------------------

CI (``.github/workflows/ci.yml``) runs on Python 3.11 and 3.12: ruff, pytest,
``sphinx-build -b html docs _build/html -W``, ``python -m build``, and
``twine check dist/*``. A separate workflow (``docs.yml``) builds the docs
and deploys GitHub Pages from ``main``. All gates must be green before
merge.

Workflow contract, abbreviated (full text in ``AGENTS.md`` and
``CONTRIBUTING.md``):

- Check ``ROADMAP.md`` before starting non-trivial work; one roadmap phase
  maps to one parent issue and one feature branch; keep ``ROADMAP.md``,
  ``CHANGE_LOG.md``, planning notes, and issue comments synchronized.
- Keep CLI commands thin wrappers over importable Python APIs.
- Keep the linear pipeline linear: continuous HiGHS LPs, no binaries, no
  thresholding or rounding of decision outputs.
- Do not commit private data, raw transcripts, credentials, generated local
  outputs, or machine-specific paths; ``tmp/``, ``local/``,
  ``data/private/``, and ``outputs/`` stay ignored.
