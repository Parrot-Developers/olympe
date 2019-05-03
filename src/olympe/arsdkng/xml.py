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

import os

import arsdkparser


class ArsdkXml(object):

    _single = None

    @classmethod
    def get(cls):
        if cls._single is None:
            cls._single = ArsdkXml()
        return cls._single

    def __init__(self):
        try:
            self.path = os.environ.get(
                "OLYMPE_XML",
                os.path.join(os.path.dirname(arsdkparser.__file__), "arsdk-xml/xml")
            )
        except KeyError:
            raise RuntimeError(
                "OLYMPE_XML environment variable doesn't exist. It should point to arsdk-xml/xml")
        self.ctx = None
        self.parse_xml()

    def parse_xml(self):
        """!
        Parse arsdk-xml files into self.ctx: ArParserCtx
        """
        self.ctx = arsdkparser.ArParserCtx()
        # first load generic.xml
        arsdkparser.parse_xml(self.ctx, os.path.join(self.path, "generic.xml"))
        for f in sorted(os.listdir(self.path)):
            if not f.endswith(".xml") or f == "generic.xml":
                continue
            arsdkparser.parse_xml(self.ctx, os.path.join(self.path, f))
