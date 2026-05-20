Operate Parrot Air SDK missions
-------------------------------

Olympe integrates with the `Parrot AirSDK <https://developer.parrot.com/docs/airsdk/general/overview.html>`_
and enables you to install AirSDK "missions" (i.e. Parrot and Parrot partners' applications):

* onto a remote drone connected to Olympe
* directly on the drone since Olympe is embedded

**Objective:**

Exchange mission specific messages with the drone.

The example below illustrates this installation process and some basic interaction with the `Air SDK
"Hello, Drone!" <https://dpc-dev.parrot.com/docs/airsdk/general/sample_hello.html#sample-hello>`_
mission.

.. literalinclude:: ../../examples/mission.py
    :language: python
    :linenos:

Olympe on drone
~~~~~~~~~~~~~~~

To benefit from the `Olympe on Drone` feature, which means running a mission on the drone from an
`Olympe` script directly embedded onto the drone, use :py:class:`olympe.LocalController`
class to connect to the drone, instead of :py:class:`olympe.Drone`.
