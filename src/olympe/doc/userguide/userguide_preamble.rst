.. _userguide_preamble:

User guide
==========

This guide will walk you through the Olympe API using a series of examples that demonstrate
increasingly advanced usage.

Before continuing, you should first read the Olympe :ref:`installation procedure<installation>`.

For your own safety and the safety of others, the following examples will use a simulated
Anafi drone but remember that Olympe is also capable of communicating with a physical
drone. As far as Olympe is concerned, the main difference between a physical and a
simulated drone is the drone’s network IP address.

.. note::

   As a reminder, a simulated drone IP is ``10.202.0.1``, a real drone IP is ``192.168.42.1``
   (over WiFi), a real SkyController IP is ``192.168.53.1``.


The full code for each example can be found in the
`src/olympe/doc/examples/ <https://github.com/Parrot-Developers/olympe/tree/master/src/olympe/doc/examples>`_
folder.

.. toctree::
   :maxdepth: 1

   Introduction <introduction>
   Environment setup <environment_setup>
   Basic examples <basics>
   Olympe eDSL <olympe_edsl>
   Advanced examples <advanced>
