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

import os
import re
from aenum import Enum


class EventMarker(Enum):
    matched, unmatched, ignored = range(3)

    def begin(self):
        return "begin_" + self.name + "("

    def end(self):
        return ").end_" + self.name

    def colored_begin(self):
        if self is EventMarker.matched:
            # green
            return "\x1b[1;32m"
        elif self is EventMarker.unmatched:
            # red
            return "\x1b[1;31m"
        else:
            # white
            return "\x1b[1;37m"

    def colored_end(self):
        return "\x1b[0m"

    @classmethod
    def color_string(cls, input_str):
        if not os.environ.get("OLYMPE_NO_COLOR"):
            out = re.sub(r"begin_(\w+)\(", lambda m: cls[m.group(1)].colored_begin(), input_str)
            out = re.sub(r"\).end_(\w+)", lambda m: cls[m.group(1)].colored_end(), out)
            return out
        else:
            return input_str
