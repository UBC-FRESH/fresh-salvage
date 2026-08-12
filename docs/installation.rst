Installation
============

``masc-yunhao-xu-linear`` is not yet published to an index. For source-checkout
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

Smoke Check
-----------

.. code-block:: bash

   masc-yunhao-xu-linear --help
   masc-yunhao-xu-linear --version

Alpha Boundary
--------------

Phase 1 ships scaffolded CLI stubs only. ``ingest``, ``ws3-run``,
``solve-principal``, ``solve-agent``, ``rh-run``, and ``export`` exit with a
not-implemented diagnostic until their roadmap phases land.
