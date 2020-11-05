# -*- coding: UTF-8 -*-

#  Copyright (C) 2019 Parrot Drones SAS
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions
#  are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the Parrot Company nor the names
#    of its contributors may be used to endorse or promote products
#    derived from this software without specific prior written
#    permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
#  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#  PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
#  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
#  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
#  OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
#  AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
#  OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.


from collections import namedtuple
from olympe.tools.error import ErrorCodeDrone
import itertools


class ReturnTuple(
        namedtuple(
            'ReturnTuple',
            ['OK', 'message', 'value', 'error_code']
        )):
    """
    A namedtuple used as a return type

    This namedtuple class definition is roughly equivalent to:

    .. code-block:: python

        typing.namedtuple(
            'ReturnTuple',
            [('OK', bool), ('message', str), ('value', typing.Any), ('error_code', int)]
        )

    A ReturnTuple is implicitly convertible to bool and evaluates to `OK`.
    """

    __slots = ()
    _iterlen = {}

    def __new__(cls, OK=False, message=None,
                value=None, error_code=None, _iterlen=None):
        self = super(ReturnTuple, cls).__new__(
            cls, OK, message, value, error_code)
        self._set_iterlen(_iterlen)
        return self

    def __nonzero__(self):
        return self.OK

    def __bool__(self):
        return self.OK

    def __eq__(self, other):
        if isinstance(other, ReturnTuple):
            return super(ReturnTuple, self).__eq__(other)
        return type(other)(self) == other

    def __ne__(self, other):
        if isinstance(other, ReturnTuple):
            return super(ReturnTuple, self).__ne__(other)
        return type(other)(self) != other

    def __iter__(self):
        return self._unpack(ReturnTuple._iterlen.get(id(self), None))

    def _unpack(self, n=None):
        if n is None:
            return super(ReturnTuple, self).__iter__()
        elif n > len(self):
            raise ValueError(
                "not enough values to unpack (expected {}, got {})".format(
                    n, len(self)))
        else:
            return itertools.islice(self._unpack(), n)

    def _set_iterlen(self, _iterlen):
        if _iterlen is not None:
            ReturnTuple._iterlen[id(self)] = _iterlen

    def _get_iterlen(self):
        return ReturnTuple._iterlen.get(id(self))

    def __getnewargs__(self):
        # used by copy / deepcopy
        return tuple(list(self._unpack()) +
                     [self._get_iterlen()])

    def __reduce__(self):
        # used by pickle
        return (_pickle_helper, self.__getnewargs__())

    @classmethod
    def _make(cls, iterable, new=tuple.__new__, len_=len):
        # overrides namedtuple._make
        if isinstance(iterable, ReturnTuple):
            iterable2 = iterable._unpack()
        else:
            iterable2 = iterable
        obj = super(ReturnTuple, cls)._make(iterable2, new=new, len=len_)
        if isinstance(iterable, ReturnTuple):
            obj._set_iterlen(iterable._get_iterlen())
        return obj

    def _replace(_self, **kwds):
        # overrides namedtuple._replace
        result = super(ReturnTuple, _self)._replace(**kwds)
        result._set_iterlen(_self._get_iterlen())
        return result

    def __del__(self):
        if ReturnTuple is None:
            return
        if id(self) in ReturnTuple._iterlen:
            del ReturnTuple._iterlen[id(self)]


def _pickle_helper(*args, **kwargs):
    # pickle seems to need a module function to do its thing
    return ReturnTuple(*args, **kwargs)


def makeReturnTuple(*args):
    """
    This is a helper function used to convert a list (ex: [ErrorCodeDrone.OK, "message"])
    to a ReturnTuple
    """
    return ReturnTuple(
        # OK
        args[0] == ErrorCodeDrone.OK,
        # message
        args[1],
        # functional value
        args[2] if len(args) > 2 else None,
        # error code
        args[0]
    )
