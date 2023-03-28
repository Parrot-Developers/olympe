.. _install_prebuilt_wheels:

Install a pre-built release via pip (x86_64 desktop only)
---------------------------------------------------------

Olympe Python Wheels Runtime Requirements
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Olympe is a Python 3 only package and requires Ubuntu 20.04 or higher, Debian 10 or higher.

Note: If you're running Linux without a graphical desktop environment installed, you'll also need to
install the `libgl1` package.

.. code-block:: console

    $ sudo apt-get install libgl1


Olympe Python Wheels minimal pip version
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Installation of Olympe wheels requires pip version 20.3 or higher. As Ubuntu and Debian latest LTS
release provided pip versions are currently too old you MUST use a `virtual environment
<https://docs.python.org/3/tutorial/venv.html>`_. See the Python environment
:ref:`best practices` below for why and how to install a Python virtual environment. The rest of
this section assumes that you have activated a suitable virtual environment in your current shell.

Install from pypi.org
^^^^^^^^^^^^^^^^^^^^^
Install the latest available version of Olympe via pip:

.. code-block:: console

    $ pip install parrot-olympe

Install from github.com
^^^^^^^^^^^^^^^^^^^^^^^
Alternatively, to install a specific Olympe version, you should browse the
https://github.com/Parrot-Developers/olympe/releases page and pip install the .whl associated to
this version, for example:

.. code-block:: console

    $ pip install https://github.com/Parrot-Developers/olympe/releases/download/v{{ release }}/parrot_olympe-{{ release }}-py3-none-manylinux_2_27_x86_64.whl
