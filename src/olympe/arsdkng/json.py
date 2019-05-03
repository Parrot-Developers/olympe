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
import re
import json

from .enums import ArsdkBitfield, ArsdkEnum, ArsdkEnums
from .messages import ArsdkMessage, ArsdkMessages


class JSONEncoder(json.JSONEncoder):

    def default(self, o):
        if issubclass(o.__class__, ArsdkBitfield):
            return "olympe.enums." + o.__class__._feature_name_ + '.' + str(o)
        elif issubclass(o.__class__, ArsdkEnum):
            return 'olympe.enums.' + o.__class__._feature_name_ + '.' + str(o)
        elif issubclass(o.__class__, ArsdkMessage):
            return 'olympe.messages.' + o.feature_name + '.' + str(o)
        return super(JSONEncoder, self).default(o)


def replace(r, d):
    ret = dict()
    for k, v in d.items():
        k = r(k)
        if isinstance(v, dict):
            v = replace(r, v)
        elif isinstance(v, list):
            v = [r(e) for e in v]
        else:
            v = r(v)
        ret[k] = v
    return ret


re_enums = re.compile(r"^olympe\.enums\.(?P<feature>[^\.]+)\.(?P<enum>[^\.]+)\.(?P<enum_val>[^\.]+)$")
re_messages = re.compile(r"^olympe\.messages\.(?P<feature>[^\.]+)\.(?P<class>[^\.]+)(\.|)(?(3)(?P<message>[^\.]+))$")


def replace_arsdk(s):
    if not isinstance(s, (str, bytes)):
        return s
    m = re_enums.match(s)
    if m:
        return ArsdkEnums.get()._by_feature[m.group("feature")][m.group("enum")][m.group("enum_val")]
    m = re_messages.match(s)
    if m:
        message = m.groupdict()
        if not message["message"]:
            return ArsdkMessages.get().by_feature[message["feature"]][message["class"]]
        else:
            return ArsdkMessages.get().by_feature[message["feature"]][message["class"]][message["message"]]
    return s


class JSONDecoder(json.JSONDecoder):

    enums = re.compile(r"^olympe\.enums\.(?P<feature>[^\.]+)\.(?P<enum>[^\.]+)\.(?P<enum_val>[^\.]+)$")
    messages = re.compile(r"^olympe\.messages\.(?P<feature>[^\.]+)\.(?P<class>[^\.]+)(\.|)(?(3)(?P<message>[^\.]+))$")

    def __init__(self, *args, **kwds):
        kwds.update(object_hook=lambda o: self._object_hook(o))
        super(JSONDecoder, self).__init__(*args, **kwds)

    def _object_hook(self, o):
        return replace(replace_arsdk, o)
