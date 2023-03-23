Connect to a physical drone
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. warning ::

    **DISCLAIMER**
    You should really carefully validate your code before trying to control a physical drone through
    Olympe. Use at your own risk.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
    FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
    INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
    BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
    OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
    AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
    OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
    OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
    SUCH DAMAGE.


Direct connection to a physical drone over Wi-Fi
""""""""""""""""""""""""""""""""""""""""""""""""

.. note::

    The direct connection to a drone over Wi-Fi (without a SkyController inbetween), is
    supported for ANAFI and ANAFI USA drones. For ANAFI Ai, the direct connection to the drone
    is disabled by default and must first be activated from FreeFlight 7 as explained
    `here <https://developer.parrot.com/docs/airsdk/general/developer_settings.html#developer-settings>`_.

To connect olympe to a physical drone, you first need to connect your Linux computer to a
drone Wi-Fi  access point. Once you are connected to your drone over Wi-Fi, you just
need to specify the drone IP address on its Wi-Fi interface ("192.168.42.1").

.. literalinclude:: ../../examples/physical_drone.py
    :language: python
    :linenos:
    :emphasize-lines: 4,8


Connect to a drone through a SkyController
""""""""""""""""""""""""""""""""""""""""""

To connect Olympe to a SkyController, you first need to connect your Linux
computer to the SkyController USB-C port. Then you should be able to connect to your
SkyController with its RNDIS IP address ("192.168.53.1") using the
:py:class:`olympe.SkyController` class instead of the :py:class:`olympe.Drone` class
that we've been using until now.

.. literalinclude:: ../../examples/physical_skyctrl.py
    :language: python
    :linenos:
    :emphasize-lines: 2,5,7

.. _skyctrl-wifi-pairing:

Wi-Fi pairing of a SkyController and a drone
""""""""""""""""""""""""""""""""""""""""""""

Wi-Fi pairing a SkyController and a drone means giving the SkyController access to the
drone Wi-Fi access point.

A SkyController keeps an internal list of "known" (previously apaired) drones. When a
SkyController boots, it scans the visible Wi-Fi SSIDs and identifies known drones
Wi-Fi access points. It then tries to connect to a every known drones that are visible
until it successfully connects to a drone.

When a SkyController 4 is connected to a drone its frontal LED displays as solid blue.

.. note::

    If you've bought your drone and your SkyController together (in the same package
    bundle), the Wi-Fi pairing of your devices has already been done at the end of
    factory assembly process and you shouldn't have to pair your devices.

There are three ways to pair a SkyController and a drone:

    1. USB pairing: just connect the SkyController and the drone with an USB-C <-> USB-C
       cable. This will actually reset the Wi-Fi security key of the drone before adding
       the drone and its new security key to the SkyController known drones list.
    2. Using the SDK `olympe.messages.drone_manager` messages to edit the SkyController
       known drones list (as demonstrated in the :ref:`following example <skyctrl-wifi-pairing-example>`)
    3. Performing a SkyController factory reset: when you've reset the SkyController to
       its factory settings, it knows the drone it was paired to at the factory.

.. _skyctrl-wifi-pairing-example:

In the following example, provided a SkyController and at least one (Wi-Fi) visible
drone, we demonstrate how to make the SkyController return its known and visible
drones list and how to add one drone to its known drones list (i.e. pairing it).

First we must connect to the SkyController using its IP address (192.168.53.1).

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: class SkyControllerExample:
    :end-at: self.skyctrl.connect()


Then, the `update_drone` method below updates the known and visible drones list.

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: def update_drones(self):
    :end-at: print("Visible drones: ", ", ".join(self.visible_drones))
    :emphasize-lines: 20-22

It then prints those two lists and possibly one "Active drone" if the SkyController is
currently connected to a drone.

The `pair_drone` method below takes a drone serial PI number and a Wi-Fi security key
and if the requested drone is currently visible will try to pair the SkyController to
it if necessary.

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: def pair_drone
    :end-before: def forget_drone
    :emphasize-lines: 10-15


Finally the `forget_drone` method below is here to demonstrate how to unpair a drone
(forget its SSID and Wi-Fi security key).

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: def forget_drone
    :end-before: def disconnect_skyctrl

The main function of this example:

    1. Connects to the SkyController
    2. Lists the visible and known drones
    3. If the requested drone is not already paired, pairs it with the SkyController
    4. If the drone was not previously paired, forgets it (this example shouldn't have
       any persistent side effect).
    5. Disconnects from the SkyController (the SkyController itself may still be
       connected to the drone though).

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: def main():
    :end-at: example.disconnect_skyctrl()

Cellular (4G) pairing of a SkyController and a drone
""""""""""""""""""""""""""""""""""""""""""""""""""""

.. note::

   Cellular pairing is only available for 4G capable drones. This currently includes
   ANAFI Ai.

Like for Wi-Fi pairing, cellular pairing a SkyController and a drone means giving the
SkyController access to the drone cellular modem interface. From the point of view of the
controller (here Olympe), a SkyController paired with a drone cellular interface acts
as a multi-path passthrough proxy to the drone (Wi-Fi + cellular).

.. note::

    While ANAFI Ai has a cellular modem, the SkyController 4 does not have one and the
    controller (the Olympe host) is used as an Internet gateway to reach the drone
    cellular interface.


Cellular pairing is only possible once the :ref:`Wi-Fi pairing <skyctrl-wifi-pairing>`
has previously been performed and when the SkyController is connected to the drone
over Wi-Fi.

.. literalinclude:: ../../examples/cellular.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: skyctrl = olympe.SkyController
    :end-at: skyctrl(connection_state(state="connected"))
    :emphasize-lines: 6

In the above example, at line 33 we check that the SkyController is currently connected
to a drone over Wi-Fi. We can then print the current status of the SkyController
cellular link to the drone (the cellular link status should not be 'RUNNING' at this point).

.. literalinclude:: ../../examples/cellular.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: # Get the cellular link status before pairing
    :end-at: print

To start the cellular pairing process, just call `skyctrl.cellular.pair()`:

.. literalinclude:: ../../examples/cellular.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: # Pair the SkyController and the Drone in cellular with a new anonymous user APC token
    :end-at: token = skyctrl.cellular.pair()

A newly created APC pairing token is returned by this function.
This APC pairing token is valid for a maximum of 2 years provided that at least one
drone is associated with this token.

.. warning::

    Do not expose your pairing APC token. The token is protecting the access to your drone and
    should be kept secret.


With your APC pairing token in your possession, you can now configure your SkyController to use it:
`skyctrl.cellular.configure(token)`.

.. literalinclude:: ../../examples/cellular.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: print("- Connect cellular using the new user APC token")
    :end-at: skyctrl.cellular.configure(token)

.. note::

   The SkyController does not store any token. You should (re-)configure a token each time you reboot
   your SkyController.


Once the SkyController has been configured with a token it will automatically try to connect to your drone using
the cellular link. We can now wait for the 'RUNNING' cellular link status.

.. literalinclude:: ../../examples/cellular.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: # Wait for cellular link status pass to Link Status.running
    :end-at: print

The SkyController is now using both the Wi-Fi and the cellular link for all network
traffic with the drone.


Commands passthrough and manual piloting
""""""""""""""""""""""""""""""""""""""""

By default, the SkyController keeps the control over the manual piloting commands with
its joysticks. For every other command and event messages, the SkyController mainly acts
as a passthrough proxy to the drone it is connected to. If you want Olympe to be able to
send manual piloting commands you should tell the SkyController that the "Controller"
(i.e. Olympe) should be the only source of manual piloting commands using the
:py:func:`olympe.messages.skyctrl.CoPiloting.setPilotingSource` command message


.. literalinclude:: ../../examples/physical_skyctrl.py
    :language: python
    :dedent:
    :linenos:
    :lineno-match:
    :start-at: skyctrl = olympe.SkyController
    :end-at: skyctrl.disconnect()
