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
twine. Python 3.11 is the minimum supported version.

External Dependencies
---------------------

``ws3`` is an external FRESH-ecosystem dependency resolved from a source
checkout, not from PyPI. Put the checkout on ``PYTHONPATH`` before running
the WS3-dependent tests or any pipeline command:

.. code-block:: bash

   export PYTHONPATH=/path/to/ws3

The femic bridge writer is imported from the femic source tree, resolved
via the ``FEMIC_SRC`` environment variable with a configured default path
as fallback. Ensemble worker processes are spawned (not forked) and inherit
``PYTHONPATH``.

Local Checks
------------

.. code-block:: bash

   python -m ruff check .
   python -m pytest
   sphinx-build -b html docs _build/html -W
   python -m build
   twine check dist/*

The suite holds 198 tests. Ruff is configured for rule sets ``E``, ``F``,
``I``, ``UP``, and ``W`` at a 100-column line length, targeting Python
3.11. Both checks must stay clean on every commit; docs-only edits should
not affect either, but verify before committing.

Workflow
--------

- Check ``ROADMAP.md`` before starting non-trivial work; keep
  ``ROADMAP.md``, ``CHANGE_LOG.md``, planning notes, and issue comments
  synchronized.
- Keep CLI commands thin wrappers over importable Python APIs.
- Parse inputs at the boundary into typed Pydantic records; keep core logic
  free of defensive re-validation.
- Keep the linear pipeline linear: continuous HiGHS LPs, no binaries, no
  thresholding or rounding of decision outputs.
- Do not commit private data, raw transcripts, credentials, generated local
  outputs, or machine-specific paths; ``tmp/``, ``local/``,
  ``data/private/``, and ``outputs/`` stay ignored.

See ``CONTRIBUTING.md`` and ``AGENTS.md`` for the full workflow contract.
