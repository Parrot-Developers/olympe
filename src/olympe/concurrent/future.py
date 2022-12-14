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


import concurrent.futures
import inspect
import threading

from ._loop import _get_running_loop


class Future(concurrent.futures.Future):

    """
    A chainable Future class
    """

    _eventloop_future_blocking = False

    def __init__(self, loop=None):
        super().__init__()
        self._loop = loop or _get_running_loop()

    def set_running_or_notify_cancel(self):
        self._register()
        return super().set_running_or_notify_cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, loop):
        if self._loop is not None:
            raise RuntimeError("Future is already attached to a loop")
        self._loop = loop

    def _register(self):
        if not self._loop:
            self._loop = _get_running_loop()
        assert self._loop
        self._loop._register_future(self)
        self.add_done_callback(lambda _: self._loop._unregister_future(self))

    def set_from(self, source):
        if self.done():
            return
        if source.cancelled() and self.cancel():
            return
        if not self.running() and not self.set_running_or_notify_cancel():
            return
        try:
            exception = source.exception()
        except:  # noqa
            self.cancel()
        else:
            if self._loop is _get_running_loop():
                self._set_from(source, exception)
            else:
                self._loop.run_async(self._set_from, source, exception)

    def _set_from(self, source, exception):
        if exception is not None:
            self.set_exception(exception)
        else:
            result = source.result()
            if not isinstance(result, Future):
                self.set_result(result)
            else:
                result.chain(self)

    def chain(self, next_):
        if self.done():
            next_.set_from(self)
        else:
            self.add_done_callback(lambda _: next_.set_from(self))

    def _then_callback(self, fn, result, deferred):
        try:
            if deferred:
                temp = self._loop.run_later(fn, self.result())
                temp.chain(result)
            elif not threading.current_thread() is self._loop:
                temp = self._loop.run_async(fn, self.result())
                temp.chain(result)
            else:
                try:
                    res = fn(self.result())
                except concurrent.futures.CancelledError:
                    result.cancel()
                except Exception as e:
                    result.set_exception(e)
                except:  # noqa
                    result.cancel()
                else:
                    if not isinstance(res, Future):
                        result.set_result(res)
                    else:
                        res.chain(result)
        except Exception as e:
            self._loop.logger.exception("Unhandled exception while chaining futures")
            result.set_exception(e)
        except:  # noqa
            result.cancel()

    def then(self, fn, deferred=False):
        result = Future(self._loop)
        if not deferred:
            deferred = inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)
        self.add_done_callback(
            lambda _: self._then_callback(fn, result, deferred=deferred)
        )
        return result

    def result_or_cancel(self, timeout=None):
        try:
            return self.result(timeout=timeout)
        except:  # noqa
            self.cancel()
            raise

    def __await__(self):
        if not self.done():
            self._eventloop_future_blocking = True
            yield self  # This tells _Task to wait for completion.
        if not self.done():
            raise RuntimeError("await wasn't used with future")
        return self.result()  # May raise too.

    __iter__ = __await__  # make compatible with 'yield from'.
