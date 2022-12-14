# Copyright © 2022 Parrot SAS; All Rights Reserved
# Copyright © 2001-2022 Python Software Foundation; All Rights Reserved
#
# SPDX-License-Identifier: PSF-2.0
#
# Licensed under the PSF License Agreement, Version 2 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://docs.python.org/3.9/license.html#psf-license
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import concurrent
import inspect
from collections import defaultdict
from dataclasses import dataclass, field, fields
from .future import Future
from ._loop import get_running_loop, _get_running_loop


class _WaitReschedule:
    pass


class _Reschedule:
    def __init__(self, deadline=None):
        self.deadline = deadline


class _Task(Future):
    """
    Adapted from asyncio.Task class under Python License
    """

    def __init__(self, loop, from_sync, corofunc, *args, **kwds):
        super().__init__(loop)
        self._from_sync = from_sync
        self._coro = corofunc(*args, **kwds)
        self._step_count = 0
        self._closed = False
        self._fut_waiter = None
        self._must_cancel = False

    def __repr__(self):
        return super().__repr__() + f" <{self._coro}>"

    def cancel(self):
        if self.done():
            return False
        if self._fut_waiter is not None:
            if self._fut_waiter.cancel():
                # Leave self._fut_waiter; it may be a Task that
                # catches and ignores the cancellation so we may have
                # to cancel it again later.
                return True
        self._must_cancel = True
        self._cancelled_exc = None
        if self._step_count == 0:
            self._coro.close()
            self._closed = True
        return True

    def _step_blocking_impl(self, blocking, result):
        # Yielded Future must come from Future.__iter__().
        if isinstance(result, Future) and result._loop is not self._loop:
            new_exc = RuntimeError(
                f"Task {self!r} got Future {result!r} attached to a different loop"
            )
            self._loop.run_later(self.step, new_exc)
        elif blocking:
            if result is self:
                new_exc = RuntimeError(f"Task cannot await on itself: {self!r}")
                self._loop.run_later(self.step, new_exc)
            elif result is None:
                # Bare yield relinquishes control for one event loop iteration.
                self._loop.run_later(self.step)
            elif type(result) is _Reschedule:
                self._loop._reschedule(self, result.deadline)
            elif type(result) is _WaitReschedule:
                pass
            else:
                result._eventloop_future_blocking = False
                result.add_done_callback(self._wakeup)
                self._fut_waiter = result
                if self._must_cancel:
                    self.cancel()
                    if self._fut_waiter.cancel():
                        self._must_cancel = False
        else:
            new_exc = RuntimeError(
                "yield was used instead of yield from "
                f"in task {self!r} with {result!r}"
            )
            self._loop.run_later(self.step, new_exc)

    def step(self, exc=None):
        if self.done():
            raise RuntimeError("Task already done")

        if self._closed:
            return

        if self._must_cancel:
            if not isinstance(exc, concurrent.futures.CancelledError):
                exc = concurrent.futures.CancelledError()
            self._must_cancel = False

        # Call either coro.throw(exc) or coro.send(None).
        _enter_task(self._loop, self)
        self._step_count += 1
        try:
            if exc is None:
                # We use the `send` method directly, because coroutines
                # don't have `__iter__` and `__next__` methods.
                result = self._coro.send(None)
            else:
                result = self._coro.throw(exc)
        except StopIteration as stop_exc:
            if self._must_cancel:
                # Task is cancelled right before coro stops.
                self._must_cancel = False
                super().cancel()
            elif exc is not None:
                super().set_exception(exc)
            else:
                super().set_result(stop_exc.value)
        except concurrent.futures.CancelledError as exc:
            # Save the original exception so we can chain it later.
            self._cancelled_exc = exc
            super().set_exception(exc)
        except (KeyboardInterrupt, SystemExit) as exc:
            super().set_exception(exc)
            raise
        except BaseException as exc:
            super().set_exception(exc)
            if self._from_sync:
                self._loop.logger.exception("Unhandled coroutine exception")
                import traceback

                self._loop.logger.error("".join(traceback.format_stack()))
        else:
            blocking = getattr(result, "_eventloop_future_blocking", None)
            if blocking is not None:
                self._step_blocking_impl(blocking, result)
            elif result is None:
                # Bare yield relinquishes control for one event loop iteration.
                self._loop.run_later(self.step)
            elif type(result) is _Reschedule:
                self._loop._reschedule(self, result.deadline)
            elif type(result) is _WaitReschedule:
                pass
            elif inspect.isgenerator(result):
                # Yielding a generator is just wrong.
                new_exc = RuntimeError(
                    "yield was used instead of yield from for "
                    f"generator in task {self!r} with {result!r}"
                )
                self._loop.run_later(self.step, new_exc)
            else:
                # Yielding something else is an error.
                new_exc = RuntimeError(f"Task got bad yield: {result!r}")
                self._loop.run_later(self.step, new_exc)
        finally:
            _leave_task(self._loop, self)
            self = None  # Needed to break cycles when an exception occurs.

    def _wakeup(self, future):
        loop = _get_running_loop()
        if loop is not None and self._loop is not loop:
            self._loop.run_async(self._wakeup, future)
            return
        try:
            future.result()
        except concurrent.futures.CancelledError as exc:
            if not self.done():
                self.step(exc)
        except BaseException as exc:
            self.step(exc)
        else:
            # Don't pass the value of `future.result()` explicitly,
            # as `Future.__await__` doesn't need it.
            self.step()
        self = None  # Needed to break cycles when an exception occurs.


_current_tasks = defaultdict(list)


def _enter_task(loop, task):
    _current_tasks[loop].append(task)


def _leave_task(loop, task):
    current_tasks_ = _current_tasks.get(loop)
    if current_tasks_ is None:
        raise RuntimeError(f"Leaving task {task!r} while no task has been entered")
    try:
        current_tasks_.remove(task)
    except ValueError:
        raise RuntimeError(f"Leaving task {task!r} that has not been entered")


def current_tasks(loop=None):
    """Return a currently executed task."""
    if loop is None:
        loop = get_running_loop()
    return _current_tasks.get(loop, [])


@dataclass(order=True)
class _TaskQueueItem:
    priority: int
    task: _Task = field(compare=False)

    def __iter__(self):
        return iter(getattr(self, f.name) for f in fields(self))
