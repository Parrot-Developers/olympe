#!/usr/bin/env python
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
from __future__ import print_function

import argparse
import os
import sys

import olympe
from sphinx.cmd.build import main as sphinx_build


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-v', '--version',
        action="store_true",
        help=u'Displays version'
    )

    parser.add_argument(
        '--gendoc',
        dest="doc_out_directory",
        help="Generate olympe documentation"
    )

    parser.add_argument(
        '--gendoc_context_path',
        dest="doc_context",
        help="Documentation context path"
    )

    ns = parser.parse_args()
    args = vars(ns)

    if args['doc_out_directory']:
        cmd = ["-b", "html"]
        if args["doc_context"]:
            cmd += ["-D", "custom_html_context_path={}".format(args["doc_context"])]
        cmd += ["{}/doc".format(os.path.dirname(olympe.__file__))]
        cmd += [args['doc_out_directory']]
        sys.exit(sphinx_build(cmd))

    if 'version' in args and args['version']:
        print(olympe.VERSION_STRING)
        sys.exit(0)
