Installation
============

``fresh-salvage`` is not yet published to a package index, so install it
from a source checkout into a repo-local virtual environment.

Requirements
------------

- Python **3.11 or newer** (``requires-python = ">=3.11"``; CI tests 3.11
  and 3.12). A system Python, pyenv, or conda base all work — only the
  version floor matters.
- Git, to check out this repository and the two external source
  dependencies below. Both come from the FRESH ecosystem, the UBC
  forestry-research software family this package belongs to.
- Enough cores and RAM for the workload you plan: the test suite and smoke
  profiles run on a laptop; the full-TSA production WS3 solve and large
  ensembles assume a workstation (the recorded benchmarks use a 64-core
  host).

Create The Environment
----------------------

Linux and macOS:

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

Windows PowerShell:

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

The runtime dependencies (``PyYAML``, ``highspy``, ``numpy``, ``pandas``,
``pyarrow``, ``pydantic``, ``rich``, ``typer``) install from PyPI with the
package. The ``[dev]`` extra adds pytest, ruff, Sphinx, the RTD theme,
build, and twine; narrower extras exist for focused work: ``[test]``
(pytest), ``[quality]`` (ruff), ``[docs]`` (Sphinx + theme), ``[release]``
(build + twine).

External Dependencies
---------------------

Three inputs come from outside PyPI. We do not ship copies of them in this
repository (they are never vendored; see the data-requirements section of
the project README):

``ws3`` (wood supply model)
   Required for ``ws3-run``, ``rh-run``, ``ensemble-run``, and the
   WS3-dependent tests. Point ``PYTHONPATH`` at a source checkout:

   .. code-block:: bash

      export PYTHONPATH=/path/to/ws3

   Without it, importing ``ws3`` stops immediately with a ``WS3Error``
   (``ws3_import_failed``) whose message names the expected resolution.

``femic`` (WS3 bridge writer)
   Required to rebuild the WS3 input files (the first
   ``ws3-run``/``rh-run``/``ensemble-run`` against a canonical femic
   bridge). The writer is imported from the femic source tree, resolved
   via the ``FEMIC_SRC`` environment variable with a configured default
   path as fallback:

   .. code-block:: bash

      export FEMIC_SRC=/path/to/femic/src

   The configured default path is machine-specific to the FRESH lab host;
   set ``FEMIC_SRC`` anywhere else. Ensemble worker processes are spawned
   (not forked) and inherit ``PYTHONPATH``, so export both variables in
   the shell that launches an ensemble.

Model inputs
   The raw VRI polygon layer (``WL_VFSL.csv``, referenced by
   ``inputs.wl_vfsl_path`` in the ingestion config) and the validated
   femic TSA29 WS3 bridge package (referenced by ``bridge_path``, with
   the femic stage-1 Woodstock CSVs as siblings of the canonical bridge)
   live outside this repository. The example configs in ``examples/``
   carry machine-specific absolute paths from the FRESH lab host —
   replace them with your own copies before running. Tracked tests and
   examples use synthetic or public-safe fixtures only.

Verify The Install
------------------

.. code-block:: bash

   fresh-salvage --help
   fresh-salvage --version        # fresh-salvage 0.1.0a1
   python -m ruff check .
   python -m pytest               # 203 tests (201 passed, 2 skipped unless
                                  # the optional ws3 checkout is on PYTHONPATH)

WS3-dependent tests expect the ``ws3`` checkout on ``PYTHONPATH``; the
end-to-end ensemble test additionally needs the local TSA29 inputs and
skips with an explicit message when they are unavailable.

With the external dependencies in place, run the deterministic WS3 smoke
profile as the pipeline-level verification gate:

.. code-block:: bash

   fresh-salvage ws3-run examples/ws3_tsa29.yaml --smoke

The smoke profile solves a 3-period horizon on the full-TSA bridge (output
under ``outputs/ws3_smoke``); the recorded regression objective is
24,328,759.75 m3 (see :doc:`validation`). All six pipeline commands
(``ingest``, ``ws3-run``, ``principal-run``, ``agent-run``, ``rh-run``,
``ensemble-run``) are implemented; see :doc:`quickstart` for a first
end-to-end run and :doc:`cli` for the full command reference.
