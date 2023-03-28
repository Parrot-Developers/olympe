Using Parrot AirSDK missions with Olympe
----------------------------------------

Olympe integrates with the `Parrot AirSDK <https://developer.parrot.com/docs/airsdk/general/overview.html>`_
and enables you to install AirSDK "missions" (i.e. Parrot and Parrot partners applications) onto
a remote drone connected to Olympe.

Once installed onto the done, Olympe is able to exchange mission specific messages with the drone.

The example below illustrate this installation process and some basic interaction with the `Air SDK
"Hello, Drone!" <https://dpc-dev.parrot.com/docs/airsdk/general/sample_hello.html#sample-hello>`_
mission.

.. literalinclude:: ../../examples/mission.py
    :language: python
    :linenos:


