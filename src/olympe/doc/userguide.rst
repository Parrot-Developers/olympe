.. _user-guide:

User guide
==========

This guide will walk you through Olympe API using a series of examples that increasingly demonstrate
more advanced usage.

Before continuing you should first read the Olympe :ref:`installation procedure<installation>`.

For your own safety and the safety of others, the following examples will use a simulated ANAFI
drone but remember that Olympe is also capable of communicating with a physical drone. As far as
Olympe is concerned, the main difference between a physical and a simulated drone is the drone IP
address (``192.168.42.1`` for a physical drone and ``10.202.0.1`` for a simulated one).


The full code of each example can be found in the
`src/olympe/doc/examples/ <https://github.com/Parrot-Developers/olympe/tree/master/src/olympe/doc/examples>`_
folder.

.. toctree::
   :maxdepth: 1

   Introduction <userguide/introduction>
   Environment setup <userguide/environment_setup>
   Basic examples <userguide/basics>
   Olympe eDSL <userguide/olympe_edsl>
   Advanced examples <userguide/advanced>
