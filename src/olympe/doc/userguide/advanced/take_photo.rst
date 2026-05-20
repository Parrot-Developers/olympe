Take photo
----------

**Objective:**

Take a photo.

First, reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal.

Copy paste the following Python ``media_ukr.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/media_ukr.py
    :language: python
    :linenos:

*Body*
~~~~~~
This example tackles 2 different way of creating a media :

* create a custom media and implement it to the :py:class:`olympe.Drone` class
* use the internal media from the :py:class:`olympe.Drone` LocalController

If you do not have special request to build your own Media, it's easier to use the one
Olympe builds itself.

The :py:func:`~olympe.messages.camera2.Configure` is necessary before any photo shooting,
or when switching from a photo mode to a recording mode (or vice versa).

Then wait for the media already present on the drone to be indexed before any shoot.

Explore :py:func:`~olympe.messages.camera2.StartPhoto`, :py:func:`~olympe.messages.camera2.StartRecording`
and all the other possibilities the `camera2` proto offers.

.. note::

    Our ``MediaEventListener`` and ``unsubscribe`` custom function are help to make sure
    the test succeeds and are not bare necessities to take a picture.

*Execution*
~~~~~~~~~~~
To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./media_ukr.py
