.. _build_olympe:

Building Olympe from source (for x86_64, armv7 or aarch64)
----------------------------------------------------------

Building Olympe from source is only necessary if you want to use Olympe on an ARM target.
If you are targetting a Linux Desktop PC (x64) you should probably use the :ref:`prebuilt wheels <install_prebuilt_wheels>` from pypi.org instead.

System requirements
^^^^^^^^^^^^^^^^^^^

The following install instructions have been tested under Ubuntu 20.04 and should also work
on Debian 10 or higher.

Download Olympe sources
^^^^^^^^^^^^^^^^^^^^^^^

Olympe sources can be downloaded on Github olympe release page: https://github.com/Parrot-Developers/olympe/releases
Download and extract the .tar.gz archive associated with the latest release of Olympe, for example:

.. code-block:: console

    $ mkdir -p ~/code/{{ workspace }}
    $ curl -L https://github.com/Parrot-Developers/olympe/releases/download/v{{ release }}/parrot-olympe-src-{{ release }}.tar.gz | tar zxf - -C ~/code/{{ workspace }} --strip-components=1
    $ cd ~/code/{{ workspace }}

System dependencies installation procedure
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

On Debian/Ubuntu, system dependencies must be installed before building Olympe from source.

.. code-block:: console

   # pyenv dependencies
   sudo apt-get install make build-essential libssl-dev zlib1g-dev \
            libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev \
            libncursesw5-dev libncurses5 xz-utils tk-dev libffi-dev liblzma-dev \
            python3-openssl git libgdbm-dev libgdbm-compat-dev uuid-dev python3-gdbm \
            gawk

    # python alchemy/dragon build system dependency
    sudo apt-get install python3

    # pdraw dependencies
    sudo apt-get install build-essential yasm cmake libtool libc6 libc6-dev \
      unzip freeglut3-dev libglfw3 libglfw3-dev libjson-c-dev libcurl4-gnutls-dev \
      libgles2-mesa-dev

    # ffmpeg alchemy module build dependencies
    sudo apt-get install rsync

    # Olympe / PySDL2 / pdraw renderer dependencies
    sudo apt-get install libsdl2-dev libsdl2-2.0-0 libjpeg-dev libwebp-dev \
     libtiff5-dev libsdl2-image-dev libsdl2-image-2.0-0 libfreetype6-dev \
     libsdl2-ttf-dev libsdl2-ttf-2.0-0 libsdl2-gfx-dev


Alternatively, to install the system dependencies of the `{{ workspace }}` workspace, just execute the `postinst` script.

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./products/olympe/linux/env/postinst


Build {{ olympe_product }}
^^^^^^^^^^^^^^^^^^^^^^^^^^

Olympe relies on some SDK C libraries that need to be built. Before using Olympe, we need to build the SDK itself.

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./build.sh -p {{ olympe_product }} -t build -j


Note: The above command needs to be done from the workspace root directory, you've
created in the previous step.

You should now have a 'built' Olympe workspace that already provides a Python virtual environment
you can use in your developments (see the next steps).

Alternatively, to build an Olympe wheel to install Olympe in another environment, use the following command:

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ ./build.sh -p {{ olympe_product }} -t images -j

Olympe wheels are built in the `out/olympe-linux/images` workspace subdirectory.

.. _environment-setup:

Set up the development environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Finally, if you want to test Olympe from your development workspace, you need to set up the shell
environment in which you will execute Olympe scripts. In the future, you will have to do this before
you execute an Olympe script from your development workspace.

To setup an interactive Olympe Python virtual environment, source the `shell` script:

.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source shell
    (olympe-python3) $ pip --version
    pip 21.3.1 from ~/code/{{ workspace }}/out/{{ olympe_product }}/pyenv_root/versions/3.9.5/lib/python3.9/site-packages/pip (python 3.9)


Note: this shell script can also be sourced from outside the workspace:

.. code-block:: console

    $ pwd
    ~/code/some/super/cool/project/path
    $ source ~/code/{{ workspace }}/shell

When an Olympe workspace Python virtual environment is active, your shell prompt should
be prefixed by
```(olympe-python3) ```.

In this console you can now execute your Olympe script, for example:

.. code-block:: console

    (olympe-python3) $ python my_olympe_script.py

Once you've finished working with Olympe, just type `exit` or press `Ctrl+D` to exit the
active environment and restore your previous prompt.

.. code-block:: console

    (olympe-python3) $ exit
    $

If you need to execute a script from a non-interactive environment (for example in a CI job),
source the `setenv` scripts instead. This script does not spawn a new shell for you,
does not change your current prompt and just sets up the environment in your current shell process.


Check your development environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you have successfuly built Olympe, the following commands shouldn't report any error.


.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source shell
    (olympe-python3) $ python -c 'import olympe; print("Installation OK")'
    $ exit


If you are following the Olympe user guide, don't forget to :ref:`set up your Python environment<environment-setup>` using the ``shell`` script before testing any Olympe example.
