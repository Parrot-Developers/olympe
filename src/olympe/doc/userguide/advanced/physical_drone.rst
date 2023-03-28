Connect to a physical drone or to a SkyController
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

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


Connect to a physical drone
"""""""""""""""""""""""""""

To connect olympe to a physical drone, you first need to connect to your linux
box to a drone Wi-Fi  access point. Once you are connected to your drone over Wi-Fi,
you just need to specify the drone ip address on its Wi-Fi interface ("192.168.42.1").

.. literalinclude:: ../../examples/physical_drone.py
    :language: python
    :linenos:


Connect to a SkyController
""""""""""""""""""""""""""

To connect Olympe to a physical SkyController, you first need to connect to your Linux
node to the SkyController 3 USB-C port. Then you should be able to connect to your SkyController
with its RNDIS IP address ("192.168.53.1").

.. literalinclude:: ../../examples/physical_skyctrl.py
    :language: python
    :linenos:


Pair a SkyController with a drone
"""""""""""""""""""""""""""""""""

If your SkyController is not already connected to a drone, you may have to pair it first.

.. literalinclude:: ../../examples/skyctrl_drone_pairing.py
    :language: python
    :linenos:

