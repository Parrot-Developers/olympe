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
