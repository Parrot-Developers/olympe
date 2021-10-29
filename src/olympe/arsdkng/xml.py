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


from pathlib import Path

import arsdkparser
import olympe_deps as od


class ArsdkXml:

    _store = {}

    @classmethod
    def get(cls, root):
        ret = cls._store.get(root)
        if ret is None:
            ret = ArsdkXml(root)
            cls._store[root] = ret
        return ret

    def __init__(self, root):
        if root == "olympe":
            # Default arsdk-xml location
            od_path = Path(od.__file__)
            if od_path.stem == "__init__":
                od_path = od_path.parent
            site_path = od_path.parent
            self.arsdk_xml_path = site_path / "arsdk" / "xml"
            self.ctx = None
            self.parse_xml()
        else:
            # TODO: we might want to support multiple version of arsdk-xml here
            self.ctx = arsdkparser.ArParserCtx()

    def parse_xml(self):
        """!
        Parse arsdk-xml files into self.ctx: ArParserCtx
        """
        self.ctx = arsdkparser.ArParserCtx()
        # first load generic.xml
        arsdkparser.parse_xml(self.ctx, str(self.arsdk_xml_path / "generic.xml"))
        for f in self.arsdk_xml_path.glob("*.xml"):
            if f.name == "generic.xml":
                continue
            arsdkparser.parse_xml(self.ctx, str(f))
