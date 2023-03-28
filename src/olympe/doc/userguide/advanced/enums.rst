Using Olympe enums types
^^^^^^^^^^^^^^^^^^^^^^^^

In addition to the :ref:`ARSDK messages<arsdk-messages-intro>`, Olympe also provides
Python Enum and Bitfield types in the `olympe.enums.<feature_name>[.<class_name>]` modules.

Most of the time, you shouldn't really need to import an enum type into your script because enum
types are implicitly constructed from a string when you create a message object, so the following
examples are roughly equivalent:

.. literalinclude:: ../../examples/enums.py
    :language: python
    :linenos:

Olympe enum types should behave very much like Python 3 `enum.Enum`.

Bitfields types are associated to each ARSDK enums types and are occasionally used by ARSDK
messages. A Bitfield type can hold any combination of its associated Enum type values.

Bitfield example:

.. literalinclude:: ../../examples/bitfields.py
    :language: python
    :linenos:

Additional usage examples are available in the unit tests of `olympe.arsdkng.enums`.

