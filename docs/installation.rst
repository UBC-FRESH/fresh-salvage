Installation
============

``fresh-salvage`` is not yet published to an index. For source-checkout
development, use a repo-local virtual environment and an editable install.

Linux and macOS
---------------

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

Windows PowerShell
------------------

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

External Dependencies
---------------------

Two FRESH-ecosystem dependencies are resolved from source checkouts rather
than PyPI:

- ``ws3`` must be importable; point ``PYTHONPATH`` at a sibling checkout
  (for example ``export PYTHONPATH=/path/to/ws3``);
- the femic bridge writer is imported from the femic source tree via the
  ``FEMIC_SRC`` environment variable, with a configured default path as
  fallback.

Model inputs (the WL_VFSL polygon layer and the validated femic TSA29 WS3
bridge) are never vendored; see the data requirements section of the
project README.

Smoke Check
-----------

.. code-block:: bash

   fresh-salvage --help
   fresh-salvage --version

All pipeline commands (``ingest``, ``ws3-run``, ``principal-run``,
``agent-run``, ``rh-run``, ``ensemble-run``) are implemented; see
:doc:`quickstart` for a first end-to-end run and :doc:`cli` for the full
command reference.
