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


from aenum import IntFlag

import concurrent.futures
import ctypes
import functools
import inspect
import logging
import olympe_deps as od
import os
import threading
import time


try:
    from itertools import ifilter as filter
except ImportError:
    # python3
    pass


logger = logging.getLogger("concurrent.futures")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


class PompEvent(IntFlag):
    IN = od.POMP_FD_EVENT_IN
    PRI = od.POMP_FD_EVENT_PRI
    OUT = od.POMP_FD_EVENT_OUT
    ERR = od.POMP_FD_EVENT_ERR
    HUP = od.POMP_FD_EVENT_HUP


class Future(concurrent.futures.Future):

    """
    A chainable Future class
    """

    _eventloop_future_blocking = False

    def __init__(self, loop=None):
        super(Future, self).__init__()
        self._loop = loop
        self._register()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, loop):
        if self._loop is not None:
            raise RuntimeError("Future is already attached to a loop")
        self._loop = loop
        self._register()

    def _register(self):
        if self._loop is not None:
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


class _Task(Future):
    """
    Adapted from asyncio.Task class under Python License
    """

    def __init__(self, loop, corofunc, *args, **kwds):
        super().__init__(loop)
        self._coro = corofunc(*args, **kwds)
        self._fut_waiter = None
        self._must_cancel = False

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
        return True

    def _step_blocking_impl(self, blocking, result):
        # Yielded Future must come from Future.__iter__().
        if isinstance(result, Future) and result._loop is not self._loop:
            new_exc = RuntimeError(
                f"Task {self!r} got Future {result!r} attached to a different"
                " loop"
            )
            self._loop.run_later(self.step, new_exc)
        elif blocking:
            if result is self:
                new_exc = RuntimeError(f"Task cannot await on itself: {self!r}")
                self._loop.run_later(self.__step, new_exc)
            else:
                result._eventloop_future_blocking = False
                result.add_done_callback(self._wakeup)
                self._fut_waiter = result
                if self._must_cancel:
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

        # Call either coro.throw(exc) or coro.send(None).
        try:
            if exc is None:
                # We use the `send` method directly, because coroutines
                # don't have `__iter__` and `__next__` methods.
                result = self._coro.send(None)
            else:
                self._coro.throw(exc)
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
        else:
            blocking = getattr(result, "_eventloop_future_blocking", None)
            if blocking is not None:
                self._step_blocking_impl(blocking, result)
            elif result is None:
                # Bare yield relinquishes control for one event loop iteration.
                self._loop.run_later(self.__step)
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
            self = None  # Needed to break cycles when an exception occurs.

    def _wakeup(self, future):
        try:
            future.result()
        except BaseException as exc:
            # This may also be a cancellation.
            self.step(exc)
        else:
            # Don't pass the value of `future.result()` explicitly,
            # as `Future.__iter__` and `Future.__await__` don't need it.
            # If we call `_step(value, None)` instead of `_step()`,
            # Python eval loop would use `.send(value)` method call,
            # instead of `__next__()`, which is slower for futures
            # that return non-generator iterators from their `__iter__`.
            self.step()
        self = None  # Needed to break cycles when an exception occurs.


class PompLoopThread(threading.Thread):
    """
    Class running a pomp loop in a pomp thread.
    It performs all calls to pomp and arsdk-ng within the loop (except init and destruction)
    """

    def __init__(self, logger, name=None, parent=None):
        self.logger = logger

        if parent is None:
            parent = threading.current_thread()
        self.parent = parent

        self.running = False
        self.pomptimeout_ms = 100
        self.async_pomp_task = list()
        self.deferred_pomp_task = list()
        self.wakeup_evt = od.pomp_evt_new()
        self.pomp_events = dict()
        self.pomp_event_callbacks = dict()
        self.pomp_loop = None
        self.pomp_timers = {}
        self.pomp_timer_callbacks = {}
        self.evt_userdata = dict()
        self.fd_userdata = dict()
        self.c_fd_userdata = dict()
        self.c_evt_userdata = dict()
        self.pomp_fd_callbacks = dict()
        self.cleanup_functions = []
        self.futures = []
        self.async_cleanup_running = False

        self._create_pomp_loop()

        super(PompLoopThread, self).__init__(name=name)

    def destroy(self):
        if self.running:
            # stop the thread will call self._destroy()
            self.stop()
        else:
            self._destroy()

    def _destroy(self):
        if self.pomp_loop is None:
            return
        if self.wakeup_evt is not None:
            self._remove_event_from_loop(self.wakeup_evt)
            od.pomp_evt_destroy(self.wakeup_evt)
            self.wakeup_evt = None

        # remove all fds from the loop
        self._destroy_pomp_loop_fds()

        # remove all timers from the loop
        self._destroy_pomp_loop_timers()

        # destroy the loop
        self._destroy_pomp_loop()

    def start(self):
        self.running = True
        super().start()

    def stop(self):
        """
        Stop thread to manage commands send to the drone
        """
        if not self.running:
            return False
        self.running = False
        if threading.current_thread().ident != self.ident:
            self._wake_up()
            self.join()
        return True

    def _ensure_future(self, func, *args, **kwds):
        if not inspect.iscoroutinefunction(func) and not inspect.isasyncgenfunction(
            func
        ):
            assert callable(func), (
                "_ensure_future first parameter must be callable or a coroutine, got"
                f" {type(func)}"
            )
            return Future(self), func, args, kwds
        else:
            task = _Task(self, func, *args, **kwds)
            return task, task.step, tuple(), dict()

    def run_async(self, func, *args, **kwds):
        """
        Fills in a list with the function to be executed in the pomp thread
        and wakes up the pomp thread.
        """
        future, func, args, kwds = self._ensure_future(func, *args, **kwds)

        if threading.current_thread() is not self:
            self.async_pomp_task.append((future, func, args, kwds))
            self._wake_up()
        else:
            try:
                ret = func(*args, **kwds)
            except Exception as e:
                self.logger.exception("Unhandled exception in async task function")
                future.set_exception(e)
            else:
                if not isinstance(ret, Future):
                    future.set_result(ret)
                else:
                    ret.chain(future)
        return future

    def run_later(self, func, *args, **kwds):
        """
        Fills in a list with the function to be executed later in the pomp thread
        """
        future, func, args, kwds = self._ensure_future(func, *args, **kwds)
        if threading.current_thread() is self:
            future.set_running_or_notify_cancel()
        self.deferred_pomp_task.append((future, func, args, kwds))
        return future

    def _run_delayed_wrapper(self, func):
        class Wrapper:
            def __init__(wrapper, func):
                wrapper.func = func
                wrapper.timer = None

            @functools.wraps(func)
            def __call__(wrapper, *args, **kwds):
                self.destroy_timer(wrapper.timer)
                return wrapper.func(*args, **kwds)
        return Wrapper(func)

    def run_delayed(self, delay, func, *args, **kwds):
        f = Future(self)
        func = self._run_delayed_wrapper(func)
        func.timer = self.create_timer(
            lambda *_: self.run_later(func, *args, **kwds).chain(f)
        )
        delay = int(1000 * delay)  # convert to milliseconds
        self.set_timer(func.timer, delay, 0)
        return f

    def complete_futures(self, *fs, timeout=None):
        f = Future(self)
        done_count = 0
        fs_count = len(fs)

        def waiter(f):
            nonlocal done_count
            if f.done():
                return
            done_count += 1
            if done_count == fs_count:
                f.set_result(True)

        def release_waiter(self, fut):
            if not f.done():
                fut.set_result(False)

        for f in fs:
            f.add_done_callback(waiter)
        self.run_delayed(timeout or 0, release_waiter)

        return f

    async def asleep(self, delay):
        await self.run_delayed(delay, lambda: None)

    async def _cancel_and_wait(self, fut):
        waiter = Future(self)
        fut.chain(waiter)
        fut.cancel()
        await waiter
        try:
            return fut.result()
        except concurrent.futures.CancelledError as exc:
            raise concurrent.futures.TimeoutError() from exc
        else:
            raise concurrent.futures.TimeoutError()

    def _release_waiter(self, waiter, fut):
        if not waiter.done():
            fut.set_exception(concurrent.futures.TimeoutError())

    async def await_for(self, timeout, fut, *args, **kwds):
        if timeout is None:
            return await fut

        fut, func, args, kwds = self._ensure_future(fut, *args, **kwds)
        if timeout <= 0:
            if fut.done():
                return fut.result()
            return await self._cancel_and_wait(fut)
        self.deferred_pomp_task.append((fut, func, args, kwds))

        waiter = Future(self)

        self.run_delayed(timeout, self._release_waiter, waiter, fut)
        fut.chain(waiter)
        try:
            await waiter
        except concurrent.futures.CancelledError:
            if fut.done():
                return fut.result()
            else:
                await self._cancel_and_wait(fut)
                raise

        if fut.done():
            return fut.result()
        else:
            try:
                return fut.result()
            except concurrent.futures.CancelledError as exc:
                raise concurrent.futures.TimeoutError() from exc
            else:
                raise concurrent.futures.TimeoutError()

    def _wake_up_event_cb(self, pomp_evt, _userdata):
        """
        Called when a wakeup pomp_evt is triggered.
        """
        # the pomp_evt is acknowledged by libpomp

    def _run_task_list(self, task_list):
        """
        Execute all pending functions located in the task list
        this is done in the order the list has been filled in
        """
        for i, (future, _, _, _) in enumerate(task_list[:]):
            try:
                if not future.running() and (not future.set_running_or_notify_cancel()):
                    self.logger.exception(f"Failed to run {future}")
                    del task_list[i]
            except RuntimeError:
                del task_list[i]
                self.logger.exception("Unexpected runtime error")
        while len(task_list):
            future, f, args, kwds = task_list.pop(0)
            try:
                ret = f(*args, **kwds)
            except Exception as e:
                self.logger.exception("Unhandled exception in async task function")
                future.set_exception(e)
                continue
            except:  # noqa
                future.cancel()
                self.running = False
                continue
            if isinstance(future, _Task):
                # Let Task.step do its thing
                return
            if not isinstance(ret, Future):
                future.set_result(ret)
            else:
                ret.chain(future)

    def run(self):
        """
        Thread's main loop
        """
        self._add_event_to_loop(
            self.wakeup_evt, lambda *args: self._wake_up_event_cb(*args)
        )

        # Before running our event loop we must ensure that our parent thread has already
        # started. This is necessary for example when 4 threads A, B, C and D are starting
        # concurrently with A calling B.start(), C calling D.start() while B is the parent
        # thread of D.
        parent_thread_grace_period = 1.
        deadline = time.time() + parent_thread_grace_period
        while not self.parent.is_alive():
            time.sleep(0.005)
            if deadline < time.time():
                self.running = False
                self.logger.error("Parent thread failed to start")

        # We have to monitor the parent thread exit. This is the simplest way to
        # let the parent (and/or main) thread handle the signals while still being
        # able to perform some cleanup before the process exit. If we don't monitor
        # the # main thread, this thread will hang the process when the process
        # receive SIGINT (or any other non fatal signal).
        try:
            while self.running and self.parent.is_alive():
                try:
                    self._wait_and_process()
                except RuntimeError as e:
                    self.logger.error(f"Exception caught: {e}")

                self._run_task_list(self.async_pomp_task)
                self._run_task_list(self.deferred_pomp_task)
        finally:
            self.running = False
            # Perform some cleanup before this thread dies
            self._cleanup()
            self._destroy()

    def _wait_and_process(self):
        od.pomp_loop_wait_and_process(self.pomp_loop, self.pomptimeout_ms)

    def _wake_up(self):
        if self.wakeup_evt:
            od.pomp_evt_signal(self.wakeup_evt)

    def add_fd_to_loop(self, fd, cb, fd_events, userdata=None):
        return self.run_async(
            self._add_fd_to_loop, fd, cb, fd_events, userdata=userdata
        )

    def has_fd(self, fd):
        try:
            return self.run_async(self._has_fd, fd).result_or_cancel(timeout=5)
        except concurrent.futures.TimeoutError:
            return False

    def _has_fd(self, fd):
        return bool(od.pomp_loop_has_fd(self.pomp_loop, fd) == 1)

    def _add_fd_to_loop(self, fd, cb, fd_events, userdata=None):
        if cb is None:
            self.logger.info(
                f"Cannot add fd '{fd}' to pomp loop without a valid callback function"
            )
            return None
        self.fd_userdata[fd] = userdata
        userdata = ctypes.cast(
            ctypes.pointer(ctypes.py_object(userdata)), ctypes.c_void_p
        )
        self.c_fd_userdata[fd] = userdata
        self.pomp_fd_callbacks[fd] = od.pomp_fd_event_cb_t(cb)
        res = od.pomp_loop_add(
            self.pomp_loop,
            ctypes.c_int32(fd),
            od.uint32_t(int(fd_events)),
            self.pomp_fd_callbacks[fd],
            userdata,
        )
        if res != 0:
            raise RuntimeError(
                f"Cannot add fd '{fd}' to pomp loop: {os.strerror(-res)} ({res})"
            )

    def remove_fd_from_loop(self, fd):
        return self.run_async(self._remove_fd_from_loop, fd)

    def _remove_fd_from_loop(self, fd):
        self.fd_userdata.pop(fd, None)
        self.c_fd_userdata.pop(fd, None)
        if self.pomp_fd_callbacks.pop(fd, None) is not None:
            if od.pomp_loop_remove(self.pomp_loop, fd) != 0:
                self.logger.error(f"Cannot remove fd '{fd}' from pomp loop")
                return False
        return True

    def add_event_to_loop(self, *args, **kwds):
        """
        Add a pomp event to the loop
        """
        return self.run_async(self._add_event_to_loop, *args, **kwds)

    def _add_event_to_loop(self, pomp_evt, cb, userdata=None):
        evt_id = id(pomp_evt)
        self.pomp_events[evt_id] = pomp_evt
        self.pomp_event_callbacks[evt_id] = od.pomp_evt_cb_t(cb)

        self.evt_userdata[evt_id] = userdata
        userdata = ctypes.cast(
            ctypes.pointer(ctypes.py_object(userdata)), ctypes.c_void_p
        )
        self.c_evt_userdata[evt_id] = userdata
        res = od.pomp_evt_attach_to_loop(
            pomp_evt, self.pomp_loop, self.pomp_event_callbacks[evt_id], userdata
        )
        if res != 0:
            raise RuntimeError("Cannot add eventfd to pomp loop")

    def remove_event_from_loop(self, *args, **kwds):
        """
        Remove a pomp event from the loop
        """
        return self.run_async(self._remove_event_from_loop, *args, **kwds)

    def _remove_event_from_loop(self, pomp_evt):
        evt_id = id(pomp_evt)
        self.evt_userdata.pop(evt_id, None)
        self.c_evt_userdata.pop(evt_id, None)
        self.pomp_event_callbacks.pop(evt_id, None)
        if self.pomp_events.pop(evt_id, None) is not None:
            if od.pomp_evt_detach_from_loop(pomp_evt, self.pomp_loop) != 0:
                self.logger.error(f"Cannot remove event '{evt_id}' from pomp loop")

    def _destroy_pomp_loop_fds(self):
        evts = list(self.pomp_events.values())[:]
        for evt in evts:
            self._remove_event_from_loop(evt)
        fds = list(self.pomp_fd_callbacks.keys())[:]
        for fd in fds:
            self._remove_fd_from_loop(fd)

    def _create_pomp_loop(self):

        self.logger.info("Creating pomp loop")
        self.pomp_loop = od.pomp_loop_new()

        if self.pomp_loop is None:
            raise RuntimeError("Cannot create pomp loop")

    def _destroy_pomp_loop(self):
        if self.pomp_loop is not None:
            res = od.pomp_loop_destroy(self.pomp_loop)

            if res != 0:
                self.logger.error(f"Error while destroying pomp loop: {res}")
                return False
            else:
                self.logger.info(f"Pomp loop has been destroyed: {self.name}")
        self.pomp_loop = None
        return True

    def create_timer(self, callback):
        self.logger.debug("Creating pomp timer")
        pomp_callback = od.pomp_timer_cb_t(lambda *args: callback(*args))
        pomp_timer = od.pomp_timer_new(self.pomp_loop, pomp_callback, None)
        if pomp_timer is None:
            raise RuntimeError("Unable to create pomp timer")

        self.pomp_timers[id(pomp_timer)] = pomp_timer
        self.pomp_timer_callbacks[id(pomp_timer)] = pomp_callback
        return pomp_timer

    def set_timer(self, pomp_timer, delay, period):
        res = od.pomp_timer_set_periodic(pomp_timer, delay, period)

        return res == 0

    def clear_timer(self, pomp_timer):
        res = od.pomp_timer_clear(pomp_timer)

        return res == 0

    def destroy_timer(self, pomp_timer):
        if id(pomp_timer) not in self.pomp_timers:
            return False

        res = od.pomp_timer_destroy(pomp_timer)

        if res != 0:
            self.logger.error(f"Error while destroying pomp loop timer: {res}")
            return False
        else:
            del self.pomp_timers[id(pomp_timer)]
            del self.pomp_timer_callbacks[id(pomp_timer)]
            self.logger.debug("Pomp loop timer has been destroyed")

        return True

    def _destroy_pomp_loop_timers(self):
        pomp_timers = list(self.pomp_timers.values())[:]
        for pomp_timer in pomp_timers:
            self.destroy_timer(pomp_timer)

    def register_cleanup(self, fn):
        self.cleanup_functions.append(fn)

    def unregister_cleanup(self, fn, ignore_error=False):
        try:
            self.cleanup_functions.remove(fn)
        except ValueError:
            if not ignore_error:
                self.logger.error(f"Failed to unregister cleanup function '{fn}'")

    def _collect_futures(self):
        self.futures = list(filter(lambda f: f.running(), self.futures))

    def _cleanup(self):
        # Execute cleanup functions
        for cleanup in reversed(self.cleanup_functions):
            try:
                cleanup()
            except Exception:
                self.logger.exception("Unhandled exception in cleanup function")
        self.cleanup_functions = []

        # Execute asynchronous cleanup actions
        timeout = 3.0  # seconds
        count_timeout = 1000 * float(timeout) / self.pomptimeout_ms
        count = 0
        self.async_cleanup_running = True
        while self.async_pomp_task or self.deferred_pomp_task or self.futures:
            self._wait_and_process()
            self._run_task_list(self.async_pomp_task)
            self._run_task_list(self.deferred_pomp_task)
            self._collect_futures()
            if count > count_timeout:
                self.logger.error(
                    f"Deferred cleanup action are still pending after {timeout}s"
                )
                break
            count += 1

        if self.futures:
            self.logger.warning(f"Futures still running: {len(self.futures)}")

        self.async_pomp_task = []
        self.deferred_pomp_task = []
        self.futures = []
        self.async_cleanup_running = False

    def _register_future(self, f):
        self.futures.append(f)

    def _unregister_future(self, f, ignore_error=False):
        try:
            self.futures.remove(f)
        except ValueError:
            if not self.async_cleanup_running and not ignore_error:
                self.logger.error(f"Failed to unregister future '{f}'")
