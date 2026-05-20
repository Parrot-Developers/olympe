Handle Olympe enums and bitfield types
---------------------------------------

In addition to the :ref:`ARSDK messages<messages-reference-documentation>`, Olympe also
provides Python Enum and Bitfield types in the `olympe.enums.<feature_name>[.<class_name>]`
modules.

Enums
^^^^^

Most of the time, you don't have to import an enum type into your script because enum
types are implicitly constructed from a string when you create a message object.

.. literalinclude:: ../../examples/enums.py
    :language: python
    :linenos:

Olympe enum types should behave very much like Python 3 `enum.Enum`.

Bitfields
^^^^^^^^^

Bitfields types are associated with each ARSDK enums types and are occasionally used by
ARSDK messages. A Bitfield type can hold any combination of its associated Enum type values.

.. literalinclude:: ../../examples/bitfields.py
    :language: python
    :linenos:

Additional usage examples are available in the unit tests of `olympe.messaging.arsdk_enums`.

