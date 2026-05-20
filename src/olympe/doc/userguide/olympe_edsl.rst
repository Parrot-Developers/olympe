.. _Olympe eDSL:

Olympe Expectation objects and the Olympe eDSL
----------------------------------------------

Before introducing more advanced features, it seems important to take a moment to have a
better understanding of the specific way Olympe uses Python operators to compose
"Expectation" objects inside the :py:class:`olympe.Drone` class.


Olympe Expectation objects
^^^^^^^^^^^^^^^^^^^^^^^^^^

"Expectation" objects are a special kind of
"`Future <https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future>`_"-like
objects from the Python `stdlib`.

.. note::

    People more familiar with Javascript might want to compare Expectation classes with the
    "`Promise <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise>`_"
    class from the standard.

**Olympe creates Expectation objects whenever a message object is "called".**

For example, ``takeoff_expectation = TakeOff()`` creates a ``takeoff_expectation`` object
from the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message we've
been using in the previous example.

**Creating an expectation object has no side effect. You must pass the expectation to a
``Drone()`` object to "schedule" it.**

BUT! ``drone(takeoff_expectation)`` will "schedule" the ``takeoff_expectation``.
Here "scheduling" this expectation means sending the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` message to the drone
and wait for the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` message
default expectations (``FlyingStateChanged(state="takingoff")`` in that case).

This means that an expectation object:
    - has a potential side effect when it is "scheduled" by a :py:class:`olympe.Drone` object (here the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command is sent)
    - may have "sub-expectation(s)" (here the ``FlyingStateChanged(state="takingoff")`` event message)

For convenience, the :py:class:`olympe.Drone` class returns the expectation object it has received in
parameter. This enables the possibility to create and schedule an expectation object in one
expression, for example: ``takeoff_expectation = drone(TakeOff())``.


Olympe Expectation eDSL
^^^^^^^^^^^^^^^^^^^^^^^

Now that we know that one Expectation object can be comprised of other expectation objects,
we might want to compose expectation objects ourselves.

Olympe supports the composition of expectation objects with 3 Python binary operators:
``|`` ("OR"), ``&`` ("AND"), and ``>>`` ("AND THEN"). This feature has been briefly
introduced in the :ref:`move_around` previous example where the ``>>``
"AND THEN" operator is used to wait for the 'hovering' flying state after a :py:func:`~olympe.messages.ardrone3.Piloting.moveBy`
command.

.. code-block:: python

    expectation_object = drone(
        moveBy(2, 0, 0, 0, _no_expect=True)
        >> FlyingStateChanged(state="flying")
        >> moveByEnd(error="ok")
        >> FlyingStateChanged(state="hovering")
    )

This specific syntax, which makes use of the Python operator overloading feature, is what
is called an "`embedded Domain Specific Language
<https://en.wikipedia.org/wiki/Domain-specific_language#External_and_Embedded_Domain_Specific_Languages>`_"
and we might refer to it as the Olympe "Expectation eDSL".

Here, the :py:class:`olympe.Drone` class accepts more than just one command message expectation.
``Drone()`` takes an expression that may be a combination of command and event messages
to process. This expression actually results in the creation of a compound expectation
object. The ">>" operator is used to combine two expressions with an "AND THEN" semantic.

Usage of :py:meth:`olympe.Expectation.wait`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
You can choose to schedule an Olympe eDSL expression with or without waiting for the end
of its execution. Call :py:meth:`olympe.Expectation.wait`
on the expectation object to block the current thread
until the expectation object is done (i.e. successful or timed out).

When a compound expectation fails (or times out) you might want to understand what went
wrong. To that end, you can use the :py:meth:`olympe.Expectation.explain` method that returns a string representation
of the compound expectation. This method highlights in green the part of the expectation
that was successful and in red the part of the compound expectation that has failed.


Programmatic eDSL construction
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You also have the ability to construct a compound expectation object programmatically before
scheduling it, for example:

.. code-block:: python

    expectation_object = (
        moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
    )
    for i in range(3):
        expectation_object = expectation_object (
            >> moveBy(10, 0, 0, 0)
            >> FlyingStateChanged(state="hovering", _timeout=5)
        )
    drone(expectation_object).wait(60)
    assert expectation_object.success()

Usage of ``_timeout``
~~~~~~~~~~~~~~~~~~~~~
Each expectation part of a compound expectation may be given a specific `_timeout` value
(in seconds) that is independent of the global compound expectation timeout value that
may be specified later in the :py:meth:`olympe.Expectation.wait` method. When
:py:meth:`olympe.Expectation.wait` is called without a
timeout value, this method blocks the current thread indefinitely until the expectation
succeeds or until a blocking sub-expectation has timed out.

In the example above, if any of the :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged` expectations times out after
5 seconds, the call to ``drone(expectation_object).wait(60)`` returns and
``expectation_object.success()`` would return ``False``. Likewise, if the drone takes
more than 60 seconds to complete this ``moveBy``, the ``expectation_object`` compound
expectation times out and ``expectation_object.success()`` returns ``False`` even if no
individual ``FlyingStateChanged`` expectation has timed out.

Olympe eDSL operators semantic
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To conclude this quick tour of the expectation eDSL, let's focus on the specific semantics
of each of the supported operators.

The ``>>`` "AND THEN" operator schedules and awaits the left-hand side expectation first.
When the left-hand side expectation is satisfied, the right-hand side expectation is
scheduled and awaited. If the left-hand side expectation times out, the right-hand side
is never scheduled nor awaited and the compound expectation times out. The compound
"AND THEN" expectation is successful when the right-hand side is successful.

The ``&`` "AND" operator schedules and awaits both the left-hand side and right-hand side
expectation objects simultaneously. The compound expectation times out if the left-hand
side or the right-hand side of the expectation times out. The compound "AND" expectation
is successful when both the left-hand side and the right-hand side expectation is
successful, without any specific order requirement.

The ``|`` "OR" operator schedules and awaits both the left-hand side and right-hand side
expectation objects simultaneously. The compound expectation times out if both the
left-hand side and the right-hand side of the expectation times out. The compound "OR"
expectation is successful if one of the left-hand side and the right-hand side
expectation is successful (or both).

You should now understand the basics of Olympe and should be able to write your own
scripts. The rest of this guide will walk you through the most advanced features of
Olympe.

