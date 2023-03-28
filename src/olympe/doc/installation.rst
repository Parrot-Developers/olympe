.. _installation:

Installation
============

Installing Olympe on Ubuntu 22.04 (on x64) or higher should be as simple as:

.. code-block:: console

    pip3 install parrot-olympe

Installation of Olympe from pre-built wheels requires pip version 20.3 or higher.
Please check our detail installation procedure :ref:`here <install_prebuilt_wheels>`.

If you need some guidance to setup your Python virtual environment you should check our
Python environment setup :ref:`best practices <best practices>`.

There is no pre-built Olympe release for ARM targets (aach64, arvmv7, ...).
To use Olympe on an ARM you need to :ref:`build it from source <build_olympe>`.

.. toctree::
   :hidden:

   install_prebuilt_wheels
   build
   pip_on_debian_based_distros
