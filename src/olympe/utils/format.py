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


import subprocess


def columns(strs, col_nb=None, aligns='<', vsep='', hsep=None):
    """
    Format a collection of strings (strs) into multiple columns.
    If the number of columns (col_nb) is unspecified, the current terminal width
    is used to determine the maximum number of columns without line split that
    it is possible to format.
    @param strs: input list of string to format
    @param col_nb: the number of column, the default depends on the input and
     the current terminal size
    @param aligns: the alignment for each column, defaults to left alignment
    @param vsep: optional vertical column separator
    @param hsep: optional horizontal row separator
    """

    # pre-process function parameters
    col_nb, cols_size, line_width = _columns_param(
        strs, col_nb=col_nb, vsep=vsep)

    if not aligns:
        aligns = '<'
    if len(aligns) == 1:
        aligns = aligns * col_nb
    elif len(aligns) < col_nb:
        aligns += aligns[-1] * (col_nb - len(aligns))

    if hsep is None:
        hsep = '\n'
    else:
        hsep = f'\n{hsep * line_width}\n'

    # build the row format string
    row_fmt = vsep.join(['{{:{}{}}}'.format(
        align, cols_size[i]) for align, i in zip(aligns, list(range(col_nb)))])

    # format each row of input
    item = iter(strs)
    rows = []
    while True:
        try:
            rows += [
                row_fmt.format(next(item), *(
                    next(item, "") for i in range(col_nb - 1)))]
        except StopIteration:
            break

    # join the rows and return the formatted text
    return hsep.join(rows)


def _term_width(default=200):
    p = subprocess.Popen('stty size', stdout=subprocess.PIPE, shell=True)
    p.wait()
    if p.returncode == 0:
        return int(p.stdout.read().split()[1])
    else:
        return default


def _columns_param(strs, col_nb, vsep):
    max_width = _term_width()
    col_nb_max = len(strs)
    params = None
    if col_nb is not None:
        col_nb_candidates = [min(col_nb_max, col_nb)]
    else:
        col_nb_candidates = list(range(1, col_nb_max + 1))
    for col_nb in col_nb_candidates:
        cols_size = {
            i: max(list(map(len, strs[i::col_nb]))) + 1 + len(vsep)
            for i in range(col_nb)
        }
        line_width = sum(cols_size.values()) + (col_nb - 1) * len(vsep)
        if params is not None and line_width > max_width:
            break
        if params is None or params[2] < line_width:
            params = (col_nb, cols_size, line_width)
    return params
