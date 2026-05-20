.. _installation_prebuilt_wheel:

Install a pre-built release via `pip` (x86_64 desktop only)
-----------------------------------------------------------

Runtime requirements
^^^^^^^^^^^^^^^^^^^^

Olympe is a Python3 only package and requires Ubuntu 20.04 or higher, Debian 10 or higher.

.. note::

    If you're running Linux without a graphical desktop environment installed, you
    also need to install the ``libgl1`` package.

    .. code-block:: console

        $ sudo apt-get install libgl1


Minimal `pip` version
^^^^^^^^^^^^^^^^^^^^^

Installation of Olympe wheels requires `pip` version 20.3 or higher. Because `pip`
versions of Ubuntu and Debian latest LTS releases are currently too old, you must use a
`virtual environment <https://docs.python.org/3/tutorial/venv.html>`_. See the :ref:`best
practices <python_environment_best_practices>` for why and how to install a Python virtual
environment.

.. note::

    The rest of this section assumes that you have activated an appropriate virtual
    environment in your shell.


Install from pypi.org
^^^^^^^^^^^^^^^^^^^^^

Install the latest available version of Olympe via `pip`:

.. code-block:: console

    $ pip install parrot-olympe

Install from github.com
^^^^^^^^^^^^^^^^^^^^^^^
Alternatively, to install a specific Olympe version, you should browse the
https://github.com/Parrot-Developers/olympe/releases page and `pip` install the ``.whl``
associated with this version, for example:

.. code-block:: console

    $ pip install https://github.com/Parrot-Developers/olympe/releases/download/v{{ release }}/parrot_olympe-{{ release }}-py3-none-manylinux_2_27_x86_64.whl
