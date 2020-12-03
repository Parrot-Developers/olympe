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


from __future__ import unicode_literals
from __future__ import absolute_import
from future.builtins import str, bytes

import ctypes
import functools
import six

from collections import OrderedDict
from datetime import datetime
from logging import getLogger
from math import isclose


from .return_tuple import ReturnTuple, makeReturnTuple  # noqa


def py_object_cast(c_pointer):
    if isinstance(c_pointer, six.integer_types):
        c_pointer = ctypes.c_void_p.from_address(c_pointer)
    if not c_pointer:
        return None
    return ctypes.cast(c_pointer, ctypes.py_object).value


class FuncDecoratorMeta(type):
    def __call__(cls, *args, **kwds):
        def _create(f):
            if f is None:
                return None
            d = super(FuncDecoratorMeta, cls).__call__(f)
            d._set_args(*args, **kwds)
            return d

        return _create


class decorator(metaclass=FuncDecoratorMeta):
    def __init__(self, f):
        self._f = f
        self._args = None
        self._kwds = None

    def _set_args(self, *args, **kwds):
        self._args = args
        self._kwds = kwds

    @property
    def func(self):
        return self._f

    @property
    def args(self):
        return self._args

    @property
    def kwds(self):
        return self._kwds

    @functools.lru_cache(maxsize=None)
    def __get__(self, obj, owner=None):
        return functools.wraps(self._f)(
            lambda *args, **kwds: self._method_call(obj, *args, **kwds)
        )

    def _method_call(self, this, *args, **kwds):
        return self.__call__(this, *args, **kwds)


class callback_decorator(decorator):
    def __call__(self, *args, **kwargs):
        try:
            return self.func(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            if "logger" in self.kwds:
                logger = self.kwds["logger"]
            elif not args:
                logger = getLogger("olympe.callbacks")
            elif hasattr(args[0], "logger"):
                logger = args[0].logger
            else:
                logger = getLogger("olympe.callbacks")
            logger.exception("Unhandled exception")
            return None


def string_from_arsdkxml(_input):
    """
    This is an utility function that convert any string (or object) coming from arsdkparser to
    a unicode string where ascii escape sequences have been processed (ex:"\\n" -> "\n").
    """
    if not _input:
        # Handles empty string and None (we don't actually handle boolean type)
        return u''
    errors = 'strict'
    if isinstance(_input, bytes):
        # str input must be decoded to unicode first
        output = _input.decode('utf-8', errors=errors)
    elif isinstance(_input, str):
        output = _input
    elif callable(_input):
        output = _input()
    else:
        # try to serialize the object to unicode
        output = bytes(_input).decode('utf-8', errors=errors)
    output = output.replace(r'\n', '\n')
    # Finally return a unicode 'string_escaped' string
    return output


def merge_mapping(mappings):
    result = OrderedDict()
    for mapping in mappings:
        for k, v in mapping.items():
            if k not in result:
                if isinstance(v, list):
                    result[k] = v[:]
                else:
                    result[k] = [v]
            elif isinstance(v, list):
                result[k].extend(v)
            else:
                result[k].append(v)
    return result


def timestamp_now():
    return (datetime.now() - datetime(1970, 1, 1)).total_seconds()


DEFAULT_FLOAT_TOL = (1e-7, 1e-9)


def equals(a, b, float_tol=DEFAULT_FLOAT_TOL):
    """
    Olympe own definition of equality between two values a and b.
    For floats, just returns the result isclose(a, b, rel_tol=1e-7, abs_tol=1e-9).
    For everything else, returns a == b.

    Remark, for floats the 1e-7 relative tolerance is equivalent to a ~33cm delta for GPS
    coordinates in decimal degrees.
    """
    if isinstance(a, float) and isinstance(b, float):
        rel_tol, abs_tol = float_tol
        return isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
    else:
        return a == b
