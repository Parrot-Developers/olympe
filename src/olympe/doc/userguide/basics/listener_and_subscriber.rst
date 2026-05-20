Listen and subscribe to an event
--------------------------------

**Objective:**

Listen to a specific event message from the drone, and react to its reception.

Listener
^^^^^^^^

The example below illustrates how to listen to an Event message from the drone:

.. literalinclude:: ../../examples/listener.py
    :language: python
    :linenos:


*Body*
~~~~~~
The decorator ``@olympe.listen_event`` takes as argument the ARSDK Event message to monitor.
This very event takes as argument :py:class:`ExpectPolicy`, as explained :ref:`here<expect_policy>`.

The function decorated is named after the event listened to: ``def onEventListenedTo(self, event, scheduler)``.
The body of this function can log or process any kind of action, especially when it affects
:py:class:`olympe.Event` and its API.

Using ``with`` affixed to an ``olympe.EventListener`` does ``subscribe`` and ``unsubscribe``
on its own, but you can also decide to explicitly call :py:meth:`olympe.EventListener.subscribe`
and :py:meth:`olympe.EventListener.unsubscribe`.
Or use a 'subscriber' instead of a 'listener'.

Subscriber
^^^^^^^^^^

The example below illustrates how to subscribe to an Event message from the drone:

.. literalinclude:: ../../examples/subscriber.py
    :language: python
    :linenos:

The purpose is the same, but the actions to be taken are quite different.

*Body*
~~~~~~
The subscription has to be done directly onto the :py:class:`olympe.Drone` API. Do not
forget to unsubscribe when done, or use ``with`` statement!

For the rest, instead of a method to define, use a lambda when you subscribe to the Event
you want to monitor.