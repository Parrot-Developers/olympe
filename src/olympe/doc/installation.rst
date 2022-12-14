.. _installation:

Installation
============

Install via pip (x86_64 desktop only)
-------------------------------------

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


Build from source (for x86_64, armv7 or aarch64)
------------------------------------------------

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
    $ {{ olympe_scripts_path }}/postinst


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
    $ source {{ olympe_scripts_path }}/shell
    ({{ python_prompt }}) $ pip --version
    pip 21.3.1 from ~/code/{{ workspace }}/out/{{ olympe_product }}/pyenv_root/versions/3.9.5/lib/python3.9/site-packages/pip (python 3.9)


Note: this shell script can also be sourced from outside the workspace:

.. code-block:: console

    $ pwd
    ~/code/some/super/cool/project/path
    $ source ~/code/{{ workspace }}/{{ olympe_scripts_path }}/shell

When a Python virtual environment is active, your shell prompt should be prefixed by
```{{ python_prompt }} ```.

In this console you can now execute your Olympe script, for example:

.. code-block:: console

    ({{ python_prompt }}) $ python my_olympe_script.py

Once you've finished working with Olympe, just type `exit` or press `Ctrl+D` to exit the
active environment and restore your previous prompt.

**Please, exit any active environment now before continuing with this user guide.**

.. code-block:: console

    ({{ python_prompt }}) $ exit
    $

If you need to execute a script from a non-interactive environment (for example in a CI job),
source the `setenv` scripts instead. This script does not spawn a new shell for you,
does not change your current prompt and just sets up the environment in your current shell process.


Check your development environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If your installation succeeded, the following commands shouldn't report any error.


.. code-block:: console

    $ pwd
    ~/code/{{ workspace }}
    $ source shell
    ({{ python_prompt }}) $ python -c 'import olympe; print("Installation OK")'
    $ exit


.. _best practices:

Python environment best practices on Debian-based distros
---------------------------------------------------------

This section of the documentation is not specific to Olympe and introduce the usage of Python
virtual environment from a beginner perspective in order to avoid Python package installation
pitfalls with pip.

What's a Python virtual environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A Python virtual environment is a Python environment isolated from the system-wide Python
environment. A package installed in one virtual environment does not change anything in the
system-wide environment (or any other virtual environment). Python virtual environment can be
created by any user without any specific privileges. A "virtual env" resides in a directory
chosen by the user and contains a "site-packages" where Python packages are installed. To use a
specific virtual environment, the user usually has to source or execute a specific script that will
activate the environment, set up environment variables and change the current shell prompt. Once a
particular environment is activated, any `python` or `pip` process executed from it will use the
virtual environment "site-packages" directory instead of the system "site-packages" directory.

Why using a Python virtual environment is important
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Using `/usr/bin/python3` the system-wide Python interpreter environment for testing & development
should generally be avoided. Creating a virtual environment per development project should be the
default instead. Here is why:

1. Virtual environments allow users to pip install Python packages without breaking the
   system-wide Python environment. **Unlike popular system package managers like `apt`, `pip`
   does not manage packages dependencies once they are installed.** This means that, installing
   a package "A" that depends on a package "B", and then installing a package "C" that depends
   on an incompatible version of package "B" (a simple "pip install A" followed by
   "pip install C") WILL break package "A".

2. The system-wide Python environment is usually managed by the system package manager (apt) and
   using pip to install packages in this environment really is asking for trouble. The two
   package managers don't talk to each others and **pip will most likely break apt installed
   Python packages** even without sudoing things and using the "--user" pip flag. Even if `pip`
   does not mess around with files under `/usr` and stick to the user site-packages directory
   `~/.local/lib/pythonX.Y/site-packages` with the "--user" flag enabled, packages installed
   there will still be visible from the system Python interpreter. For example, this means you
   can break `pip` or `apt` (it also depends on Python...) with just one harmless
   `pip install --user ...` command.

3. You can't `pip install --upgrade pip` (or `python get-pip.py`) in the system environment.
   Doing this WILL break your environment sometime in very subtle ways. Installing just one
   random package with pip can result in a pip self-upgrade (if pip is a dependency of that
   package...).  When you create a Python virtual environment you're able to upgrade the version
   of `pip` inside it without any issue.

4. Outside a virtual environment, you can't rely on the `python3` package provided by Debian
   and/or Ubuntu via apt because Debian patches the interpreter (and `pip`) to behave
   differently outside a virtual environment when installing packages. The situation is messy.
   **I can't stress this enough** but the official pypa.io pip installation guide does not
   provide a viable solution to install `pip` on Debian system. **Trying to follow the pypa.io
   installation "supported methods" (ensurepip/get-pip.py) will break your Debian based Python
   environment.** The devil is in the details, but their installation procedure suppose that you
   are using an upstream Python interpreter... not the one provided by your distro.

5. Finally, you should never have to `sudo pip install ...` to install a package. Doing so is a
   beginner mistake, and you should now know why. Usually, when someone has to resort to this it
   means their environment is already broken. :)


Using one virtual environment per project allows you to have an environment isolated from the system
environment in which you can install any package (including a recent version of `pip`) without
risking to break anything.

Creating a Python virtual environment on a Debian-based system
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

On Debian-based distros you first need to ensure that the Python standard `venv` library is
available.

.. code-block:: console

    $ sudo apt-get install python3-venv

You can then create your first virtual environment:

.. code-block:: console

   $ python3 -m venv my-virtual-env

This will create a `my-virtual-env` virtual environment in the current directory.
To enter/activate this virtual environment in the current shell, you need to "source" its activation
script. The first thing you want to do in this environment is upgrading pip.

.. code-block:: console

   $ . ./my-virtual-env/bin/activate
   (my-virtual-env) $ python --version
   Python 3.8.10
   (my-virtual-env) $ pip install --upgrade pip
   ...
   (my-virtual-env) $ pip --version
   pip 21.3.1 from /home/user/my-virtual-env/lib/python3.8/site-packages/pip (python 3.8)

To deactivate/exit the virtual environment, just type `deactivate`, since we are done with this
little virtual experience, we can safely remove this virtual environment from our filesystem.

.. code-block:: console

   (my-virtual-env) $ deactivate
   $ rm -rf my-virtual-env/

Note: On Debian-based distros, you have little to no use for the apt provided `pip` (the
`python3-pip` package). I personally use it just to install `virtualenv
<https://virtualenv.pypa.io/en/latest/>`_ (a better/faster version of `venv
<https://docs.python.org/3/tutorial/venv.html>`_).
