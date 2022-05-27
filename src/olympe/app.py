#!/usr/bin/env python

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


import argparse
import os
import sys

import olympe


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-v', '--version',
        action="store_true",
        help='Displays version'
    )

    parser.add_argument(
        '--ip',
        help="Drone IP address"
    )

    parser.add_argument(
        '--gendoc',
        dest="doc_out_directory",
        help="Generate olympe documentation"
    )

    parser.add_argument(
        '--gendoc_conf',
        dest="doc_conf",
        help="Sphinx doc conf.py path (overrides the default one)"
    )

    parser.add_argument(
        '--gendoc_version',
        dest="doc_version",
        help="Override documentation version number"
    )

    parser.add_argument(
        '--gendoc_release',
        dest="doc_release",
        help="Override documentation release number"
    )

    parser.add_argument(
        '--gendoc_context_path',
        dest="doc_context",
        help="Documentation context path"
    )

    ns = parser.parse_args(argv)
    args = vars(ns)

    if args['doc_out_directory']:
        from sphinx.cmd.build import main as sphinx_build
        cmd = ["-b", "html"]
        if args["doc_context"]:
            cmd += ["-D", "custom_html_context_path={}".format(args["doc_context"])]
        if args["doc_conf"]:
            cmd += ["-c", args["doc_conf"]]
        if args["doc_version"]:
            cmd += ["-D", f"version={args['doc_version']}"]
        if args["doc_release"]:
            cmd += ["-D", f"release={args['doc_release']}"]
        cmd += [f"{os.path.dirname(olympe.__file__)}/doc"]
        cmd += [args['doc_out_directory']]
        sys.exit(sphinx_build(cmd))

    if 'version' in args and args['version']:
        print(olympe.__version__)
        sys.exit(0)

    import IPython
    user_ns = dict(olympe=olympe)
    if args["ip"]:
        user_ns["drone"] = olympe.Drone(args["ip"])
    IPython.embed(user_ns=user_ns)


if __name__ == "__main__":
    main()
