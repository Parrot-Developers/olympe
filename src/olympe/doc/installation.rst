.. _installation:

Installation
============

Olympe Python Wheels Runtime Requirements
-----------------------------------------

Olympe is Python 3 only and requires Ubuntu 20.04 or higher, Debian 10 or higher.

Install via pip (x86_64 desktop only)
-------------------------------------

Installation of Olympe wheels requires pip version 20.3 or higher. You should first check your
`pip --version` and use a `virtual environment <https://virtualenv.pypa.io/en/latest/user_guide.html>_`
if necessary.

Install the latest available version of Olympe via pip:

.. code-block:: console

    $ python3 -m pip install --user parrot-olympe

Alternatively, to install a specific Olympe version, you should browse the
https://github.com/Parrot-Developers/olympe/releases page and pip install the .whl associated to
this version, for example:

.. code-block:: console

    $ python3 -m pip install --user https://github.com/Parrot-Developers/olympe/releases/download/v7.0.1/parrot_olympe-7.0.1-py3-none-manylinux_2_27_x86_64.whl


Build from source (for x86_64, armv7 or aarch64)
================================================

System requirements
-------------------

The following install instructions have been tested under Ubuntu 20.04 and should also work
on Debian 10 or higher.

Download Olympe sources
-----------------------

Olympe sources can be downloaded on Github olympe release page: https://github.com/Parrot-Developers/olympe/releases
Download and extract the .tar.gz archive associated with the latest release of Olympe, for example:

.. code-block:: console

    $ mkdir -p ~/code/{{ workspace }}
    $ curl https://github.com/Parrot-Developers/olympe/releases/download/v7.0.1/parrot-olympe-src-7.0.1.tar.gz | tar zxf - -C ~/code/{{ workspace }} --strip-components=1
    $ cd ~/code/{{ workspace }}


Install {{ olympe_product }} dependencies
-----------------------------------------

System dependencies installation procedure
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To install the system dependencies of the "{{ workspace }}" workspace, just execute the `postinst` script.

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ {{ olympe_scripts_path }}/postinst

Build {{ olympe_product }}
--------------------------

Olympe relies on some SDK C libraries that need to be built.  Before using Olympe, We need to build the SDK itself.

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./build.sh -p {{ olympe_product }} -t build -j


Note: The above command needs to be done from the workspace root directory, you've
created in the previous step.

You should now have a 'built' Olympe workspace that already provides a Python virtual environment
you can use in your developments (see the next steps).

Alternatively, to build an Olympe wheel to install in another environment, use the following command

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./build.sh -p {{ olympe_product }} -t images -j

Olympe wheels are built in the `out/olympe-linux/images` workspace subdirectory.

.. _environment-setup:

Set up the development environment
----------------------------------

Finally, you need to set up the shell environment in which you will execute Olympe scripts.
In the future, you will have to do this before you execute an Olympe script.

To setup an interactive Olympe Python environment, source the `shell` script:

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source {{ olympe_scripts_path }}/shell
    ({{ python_prompt }}) $ pip --version
    pip 21.3.1 from ~/code/{{ workspace }}/out/{{ olympe_product }}/pyenv_root/versions/3.9.5/lib/python3.9/site-packages/pip (python 3.9)


The shell script can be sourced from outside the workspace:

.. code-block:: console

    $ pwd
    ~/code/some/super/cool/project/path
    $ source ~/code/{{ workspace }}/{{ olympe_scripts_path }}/shell

When a Python environment is active, your shell prompt should be prefixed by ```{{ python_prompt }} ```.

In this console you can now execute your Olympe script, for example:

.. code-block:: console

    ({{ python_prompt }}) $ python my_olympe_script.py

Once you've finished working with Olympe, just type `exit` or press `Ctrl+D` to exit the
active environment and restore your previous prompt.

**Please, exit any active environment now before continuing.**

.. code-block:: console

    ({{ python_prompt }}) $ exit
    $

If you need to execute a script from a non-interactive environment (for example in a CI job),
source the `setenv` or the `setenv3` scripts instead. These scripts don't spawn a new shell for you,
don't change your current prompt and just set up the environment in your current shell process.


Check your development environment
----------------------------------

If your installation succeeded, the following commands shouldn't report any error.


.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source shell
    ({{ python_prompt }}) $ python -c 'import olympe; print("Installation OK")'
    $ exit


