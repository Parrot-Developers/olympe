.. _Olympe eDSL:

Olympe Expectation objects and the Olympe eDSL
----------------------------------------------

Before introducing more advanced feature, it seems important to take a moment to have a better
understanding of the Olympe specific usage of Python operators to compose "Expectation" objects
inside the ``drone()`` functor.

Olympe Expectation objects
^^^^^^^^^^^^^^^^^^^^^^^^^^

First, let's explain what is an "Expectation" object.  "Expectation" objects are a special kind of
"`Future <https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future>`_"-like
objects from the Python stdlib. People more familiar with Javascript might want to compare
"Expectation" classes with the "`Promise <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise>`_"
class from the standard.

Olympe creates "Expectation" objects whenever a message object is "called". For example,
``takeoff_expectation = TakeOff()`` creates a ``takeoff_expectation`` object from the
``ardrone3.Piloting.TakeOff`` command message we've been using in the previous example.

Simply creating an expectation object has no side effect, you must pass the expectation object to a
``drone`` object to "schedule" it. Continuing with our previous example
``drone(takeoff_expectation)`` will "schedule" the ``takeoff_expectation``. Here "scheduling" this
expectation actually means sending the ``TakeOff`` message to the drone and wait for the ``TakeOff``
message default expectations (``FlyingStateChanged(state="takingoff")``). Let us pause on that. This
means that an expectation objects:
- has a potential side effect when it is "scheduled" by a ``drone`` object (here we send the
``TakeOff`` message)
- may have "sub-expectation(s)" (here the ``FlyingStateChanged(state="takingoff")`` event message)

For convenience, the ``drone()`` functor returns the expectation object it has received in
parameter. This enables the possibility to create and schedule an expectation object in one
expression, for example: ``takeoff_expectation = drone(TakeOff())``.

Olympe Expectation eDSL
^^^^^^^^^^^^^^^^^^^^^^^

Now that we know that one "Expectation" object can be comprised of other expectation objects, like
this is the case of the ``takeoff_expectation`` in the previous paragraph, we might want to compose
expectation objects ourselves.

Olympe supports the composition of expectation objects with 3 Python binary operators:
``|`` ("OR"), ``&`` ("AND"), and ``>>`` ("AND THEN"). This feature has been briefly introduced in
the ``Moving around - Waiting for a 'hovering' flying state`` previous example where the ``>>``
"and then" operator is used to wait for the "hovering" flying state after a ``moveBy`` command.

.. code-block:: python

    expectation_object = drone(
        moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
    )

This specific syntax that makes use of the Python operator overloading feature is what is called
an "`embedded Domain Specific Language
<https://en.wikipedia.org/wiki/Domain-specific_language#External_and_Embedded_Domain_Specific_Languages>`_"
and we might refer to it as the Olympe "Expectation eDSL".

Here, the ``drone()`` functor accepts more than just one command message expectation. The
``drone()`` functor takes an expression that may be a combination of command and event messages to
process. This expression actually results in the creation of a compound expectation object.
The ">>" operator is used to combine two expressions with an "and then" semantic. This example
could be read as "Take off and then wait a maximum of 5 seconds for the 'hovering' flying state").

You can choose to schedule an Olympe eDSL expression with or without waiting for the end of its
execution, just call ``.wait()`` on the expectation object to block the current thread until the
expectation object is done (i.e. successful or timedout).

When a compound expectation fails (or times out) you might want to understand what went wrong. To
that end, you can use the ``.explain()`` that returns a string representation of the compound
expectation. The ``.explain()`` method highlights in green the part of the expectation that was
successful and in red the part of the compound expectation that has failed.

Programmatic eDSL construct
^^^^^^^^^^^^^^^^^^^^^^^^^^^

You also have the ability to construct a compound expectation object programmatically before
scheduling it, for example:

.. code-block:: python

    expectation_object = (
        moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
    )
    for i in range(3):
        expectation_object = expectation_object
            >> moveBy(10, 0, 0, 0)
            >> FlyingStateChanged(state="hovering", _timeout=5)
        )
    drone(expectation_object).wait(60)
    assert expectation_object.success()


Each expectation part of a compound expectation may be given a specific `_timeout` value in seconds
that is independent of the global compound expectation timeout value that may be specified later to
the ``.wait()`` method. When ``.wait()`` is called without a timeout value, this method blocks the
current thread indefinitely until the expectation succeeds or until a blocking sub-expectation has
timedout. In the example above, if any of the ``FlyingStateChanged`` expectations times out after 5
seconds, the call to ``drone(expectation_object).wait(60)`` returns and
``expectation_object.success()`` would return ``False``. Likewise, if the drone takes more than 60
seconds to complete this ``moveBy``, the ``expectation_object`` compound expectation times out and
``expectation_object.success()`` returns ``False`` even if no individual ``FlyingStateChanged``
expectation has timedout.

Olympe eDSL operators semantic
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To conclude this quick tour of the expectation eDSL, let's focus on the specific semantic of each
of the supported operator.

The ``>>`` "and then" operator is probably the most useful of them. When an "and then" compound
expectation is scheduled, the left hand side expectation is scheduled and awaited. When the left
hand side expectation is satisfied the right-hand side expectation is scheduled and awaited. If the
left-hand side expectation times out, the left-hand side is never scheduled nor awaited and the
compound expectation times out. The compound "and then" expectation is successful when the
right-hand side is successful.

The ``&`` "and" operator schedules and awaits both the left-hand side and right-hand side
expectations objects simultaneously. The compound "and" expectation is successful when both the
left-hand side and the right hand side expectation are successful without any specific order
requirement. The compound expectation times out if the left-hand side or the right-hand side of the
expectation times out.

The ``|`` "or" operator schedules and awaits both the left-hand side and right-hand side
expectations objects simultaneously. The compound "or" expectation is successful if one of the
left-hand side and the right hand side expectation is successful (or both). The
compound expectation times out if both the left-hand side and the right-hand side of the expectation
times out.

You should now understand the basics of Olympe and should be able to write your own scripts.
The rest of this guide will walk you through the most advanced (nevertheless important) features of
Olympe.

