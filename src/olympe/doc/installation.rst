.. _installation:

Installation
============

System requirements
-------------------

The following install instructions have been tested under Ubuntu 18.04 and should also work
on Debian 9 or higher.


Clone the {{ workspace }} repo workspace
-----------------------------------------

Olympe is part of the {{ workspace }} workspace so first, you need to clone that workspace
(somewhere in your home directory) using the (repo_) utility tool:

.. _repo: {{ repo_dl_url }}

.. code-block:: console

    $ cd $HOME
    $ mkdir -p code/{{ workspace }}
    $ cd code/{{ workspace }}
    $ pwd
    ~/code/{{ workspace }}
    $ repo init {{ sdk_repo_init_args }}
    $ repo sync

.. only:: internal

    By default, it's a good idea to checkout the stable.xml manifest like it is done in the command above.
    Sometime however you might need to keep up with some recent changes and might want to use one
    of the following manifests:

        - default.xml: all branches are tracked
        - release.xml: all branches are frozen on sha1 (RECOMMENDED)
        - stable.xml: based on release, but some active projects are tracking master (e.g. Olympe,
          test scripts, ...)
        - experimental.xml: based on stable, but some projects are using development branches


Install {{ olympe_product }} dependencies
-----------------------------------------

Recommended dependency installation procedure
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To install the dependencies of the "{{ workspace }}" workspace, just execute the `postinst` script.

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ {{ olympe_scripts_path }}/postinst

.. Warning::
    For Anaconda users
    You may want to manually install the Python dependencies rather than
    relying on the `postinst` script (see manual-dependency-installation_)

.. _manual-dependency-installation:

Manual dependency installation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The `postinst` script will install some global Python packages using your
system provided python-pip. Hence:

    - It will NOT install any dependency in you custom Python environment
      (e.g. Anaconda) even if your Anaconda installation
      is in your `PATH`.
    - The `build.sh` script (see below) WILL fail if your Anaconda Python is
      in your `PATH`.

As a workaround, you may choose to:
    - Remove Anaconda (or any other custom Python environment) from your
      `PATH` before running the `postinst` and `build.sh` scripts.
    - Proceed with a manual depencendy installation (see below).

.. code-block:: console

    # pdraw dependencies
    $ sudo apt-get -y install build-essential yasm cmake libtool libc6 libc6-dev \
      unzip freeglut3-dev libglfw3 libglfw3-dev libsdl2-dev libjson-c-dev \
      libcurl4-gnutls-dev libavahi-client-dev libgles2-mesa-dev

    # ffmpeg build dependencies
    $ sudo apt-get -y install rsync

    # arsdk build dependencies
    $ sudo apt-get -y install cmake libbluetooth-dev libavahi-client-dev \
        libopencv-dev libswscale-dev libavformat-dev \
        libavcodec-dev libavutil-dev cython python-dev

    # olympe build dependency
    $ pip3 install clang

Please, modify `{{ workspace }}` workspace location below according to your
local installation path.

.. code-block:: console

    # olympe python runtime dependencies
    $ pip3 install -r ~/code/{{ workspace }}/{{olympe_path}}/requirements.txt
    $ echo "export PYTHONPATH=\$PYTHONPATH:~/code/{{ workspace }}/out/olympe-linux/final/usr/lib/python/site-packages/" >> ~/code/{{ workspace }}/products/olympe/linux/env/setenv


Build {{ olympe_product }}
--------------------------

Olympe relies on some arsdk C libraries that need to be (re-)built after a repo sync.

Before using Olympe, We need to build the arsdk itself. In the future, the following command will
be needed after each `repo sync`.


.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./build.sh -p {{ olympe_product }} -A all final -j


Note: The above command needs to be done from the workspace root directory, you've
created in the previous step.

.. _environment-setup:

Set up the environment
----------------------

Finally, you need to set up the shell environment in which you will execute Olympe scripts.
In the future, you will have to do this before you execute an Olympe script.

To setup an interactive Olympe Python environment, source the `shell` script:

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source {{ olympe_scripts_path }}/shell
    ({{ python_prompt }}) $ pip --version
    pip 18.1 from ~/code/{{ workspace }}/.python/py3/local/lib/python3.6/site-packages/pip (python 3.6)


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


Check your installation
-----------------------

If your installation succeeded, the following commands shouldn't report any error.


.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source shell
    ({{ python_prompt }}) $ python -c 'import olympe; print("Installation OK")'
    $ exit


