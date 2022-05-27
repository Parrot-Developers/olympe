#  Copyright (C) 2019-2021 Parrot Drones SAS
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


from collections.abc import Iterable, Mapping
from olympe.event import Event


class ArsdkMessageArgs(dict):
    pass


class ArsdkMessageEvent(Event):
    def __init__(self, message, args, policy=None):
        self._message = message
        self._args = args
        super().__init__(policy=policy)

    @property
    def message(self):
        return self._message

    @property
    def args(self):
        return self._args

    @property
    def id(self):
        return self._message.id

    def __str__(self):
        ret = self.message.fullName + "("
        if isinstance(self.args, (list)):
            if len(self.args) == 1:
                ret += self._str_args()
            elif len(self.args):
                ret += "["
                for args in self.args:
                    ret += self._str_args()
                ret += "]"
        else:
            ret += self._str_args()
        ret += ")"
        return ret

    def _str_args(self):
        args_list = []
        for argname, argvalue in self.args.items():
            args_list += [f"{argname}={self._str_arg(argvalue)}"]
        if self.policy is not None:
            args_list += [f"policy={self.policy}"]
        return ", ".join(args_list)

    def _str_arg(self, argvalue):
        if hasattr(argvalue, "pretty"):
            return argvalue.pretty()
        elif isinstance(argvalue, str):
            return "'" + argvalue + "'"
        else:
            return str(argvalue)


class ArsdkProtoMessageEvent(Event):
    def __init__(self, message, args, policy=None):
        self._message = message
        self._args = args
        super().__init__(policy=policy)

    @property
    def message(self):
        return self._message

    @property
    def args(self):
        return self._args

    @property
    def id(self):
        return self._message.id

    def __str__(self):
        return f"{self.message.fullName}{self._str_dict(self.args)}"

    def _str(self, argvalue):
        if isinstance(argvalue, (str, bytes)):
            return f"'{argvalue}'"
        elif hasattr(argvalue, "pretty"):
            return argvalue.pretty()
        elif isinstance(argvalue, ArsdkMessageArgs):
            return f"{argvalue.__class__.__name__}{self._str_dict(argvalue)}"
        elif isinstance(argvalue, Mapping):
            return f"dict{self._str_dict(argvalue)}"
        elif isinstance(argvalue, Iterable):
            return f"{self._str_iter(argvalue)}"
        else:
            return f"{argvalue}"

    def _str_dict(self, d):
        args_list = []
        for argname, argvalue in d.items():
            args_list.append(f"{argname}={self._str(argvalue)}")
        return "(" + ", ".join(args_list) + ")"

    def _str_iter(self, i):
        args_list = []
        for argvalue in i:
            args_list.append(f"{self._str(argvalue)}")
        return "(" + ", ".join(args_list) + ")"
