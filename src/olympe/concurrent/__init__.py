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
from olympe.utils import callback_decorator
from olympe.utils.path import TemporaryFile
from .sync import (
    Lock,
    Event,
    Condition,
    Semaphore,
    BoundedSemaphore,
)
from ._loop import (
    _set_running_loop,
    get_running_loop,
)
from .future import Future
from ._task import _Task, _TaskQueueItem, _WaitReschedule, _Reschedule, current_tasks

# expose concurrent.futures.TimeoutError
from concurrent.futures import TimeoutError, CancelledError  # noqa
from queue import PriorityQueue


import concurrent.futures
import ctypes
import faulthandler
import inspect
import logging
import olympe_deps as od
import os
import threading
import time
import types


logger = logging.getLogger("concurrent.futures")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


class PompEvent(IntFlag):
    IN = od.POMP_FD_EVENT_IN
    PRI = od.POMP_FD_EVENT_PRI
    OUT = od.POMP_FD_EVENT_OUT
    ERR = od.POMP_FD_EVENT_ERR
    HUP = od.POMP_FD_EVENT_HUP


@types.coroutine
def async_yield(obj):
    return (yield obj)


async def reschedule(deadline=None):
    return await async_yield(_Reschedule(deadline))


async def wait_reschedule():
    return await async_yield(_WaitReschedule)


class Loop(threading.Thread):
    """
    Class running a pomp loop in a pomp thread.
    It performs all calls to pomp and arsdk-ng within the loop (except init and destruction)
    """

    def __init__(self, logger, name=None, parent=None, max_workers=None):
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
        self._scheduled_tasks = PriorityQueue()
        self.evt_userdata = dict()
        self.fd_userdata = dict()
        self.c_fd_userdata = dict()
        self.c_evt_userdata = dict()
        self.pomp_fd_callbacks = dict()
        self.cleanup_functions = dict()
        self.futures = set()
        self.async_cleanup_running = False
        self._watchdog_cb_imp = od.pomp_watchdog_cb_t(lambda *_: self._watchdog_cb())
        self._watchdog_user_cb = None

        self._executor = concurrent.futures.ThreadPoolExecutor(
            thread_name_prefix=f"{name}_executor", max_workers=max_workers
        )

        self._create_pomp_loop()
        self._task_timer = self.create_timer(self._task_timer_cb)

        super().__init__(name=name)

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
        if self.is_alive() and threading.current_thread().ident != self.ident:
            self._wake_up()
            self.join()
        return True

    def _task_timer_cb(self, *_):
        if self._scheduled_tasks.empty():
            return
        now = int(time.time() * 1000)
        deadline, task = self._scheduled_tasks.queue[0]
        seen = set()
        delay = deadline - now
        while delay <= 0:
            self.run_later(task.step)
            seen.add(task)
            self._scheduled_tasks.get_nowait()
            if self._scheduled_tasks.empty():
                return
            deadline, task = self._scheduled_tasks.queue[0]
            delay = deadline - now
            if task in seen:
                if self.running:
                    self.run_later(self._task_timer_cb)
                return
        if not self.running:
            while not self._scheduled_tasks.empty():
                _, task = self._scheduled_tasks.get_nowait()
                task.set_exception(CancelledError())
            return
        self.set_timer(self._task_timer, delay, 0)

    def _reschedule(self, task, deadline=None):
        now = int(time.time() * 1000)
        if deadline is not None:
            deadline = int(deadline * 1000)
        if deadline is None or deadline < now:
            self.run_later(task.step)
        else:
            current_deadline = None
            if not self._scheduled_tasks.empty():
                current_deadline = self._scheduled_tasks.queue[0].priority
            delay = deadline - now
            if current_deadline is None or current_deadline > deadline:
                if delay > 0:
                    self._scheduled_tasks.put_nowait(_TaskQueueItem(deadline, task))
                    self.set_timer(self._task_timer, delay, 0)
                else:
                    self.run_later(task.step)
            else:
                self._scheduled_tasks.put_nowait(_TaskQueueItem(deadline, task))

    def _ensure_from_sync_future(self, func, *args, **kwds):
        if not inspect.iscoroutinefunction(func) and not inspect.isasyncgenfunction(
            func
        ):
            assert callable(func), (
                "_ensure_from_sync_future first parameter must be callable or a coroutine, got"
                f" {type(func)}"
            )
            return Future(self), func, args, kwds
        else:
            task = _Task(self, True, func, *args, **kwds)
            return task, task.step, tuple(), dict()

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
            task = _Task(self, False, func, *args, **kwds)
            return task, task.step, tuple(), dict()

    def run_in_executor(self, func, *args, **kwds):
        fut = Future(self)
        self._executor.submit(func, *args, **kwds).add_done_callback(fut.set_from)
        return fut

    def run_async(self, func, *args, **kwds):
        """
        Fills in a list with the function to be executed in the pomp thread
        and wakes up the pomp thread.
        """
        future, func, args, kwds = self._ensure_from_sync_future(func, *args, **kwds)

        if threading.current_thread() is not self:
            self.async_pomp_task.append((future, func, args, kwds))
            self._wake_up()
        else:
            future.set_running_or_notify_cancel()
            try:
                ret = func(*args, **kwds)
            except Exception as e:
                self.logger.exception("Unhandled exception in async task function")
                future.set_exception(e)
            except:  # noqa
                future.cancel()
                self.running = False
            else:
                if future.done():
                    assert isinstance(future, _Task)
                    # _Task.step has set itself
                elif isinstance(future, _Task):
                    # Let _Task.step do its thing
                    pass
                elif not isinstance(ret, Future):
                    future.set_result(ret)
                else:
                    ret.chain(future)
        return future

    def run_later(self, func, *args, **kwds):
        """
        Fills in a list with the function to be executed later in the pomp thread
        """
        future, func, args, kwds = self._ensure_from_sync_future(func, *args, **kwds)
        if threading.current_thread() is self:
            future.set_running_or_notify_cancel()
        self.deferred_pomp_task.append((future, func, args, kwds))
        return future

    def _run_delayed_wrapper(self, delay, func):
        async def wrapper(*args, **kwds):
            await self.asleep(delay)
            if not inspect.iscoroutinefunction(func) and not inspect.isasyncgenfunction(func):
                func(*args, **kwds)
            else:
                await func(*args, **kwds)
        wrapper.__qualname__ = f"<delayed_wapper>{func}"
        return wrapper

    def run_delayed(self, delay, func, *args, **kwds):
        func = self._run_delayed_wrapper(delay, func)
        return self.run_async(func, *args, **kwds)

    def complete_futures(self, *fs, timeout=None):
        ret = Future(self)
        done_count = 0
        exception = None
        fs_count = len(fs)

        def waiter(fut):
            nonlocal ret
            nonlocal done_count
            nonlocal fs_count
            nonlocal exception

            if ret.done():
                return

            if fut.exception() is not None:
                exception = fut.exception()
            done_count += 1
            if done_count == fs_count:
                if exception is not None:
                    ret.set_exception(exception)
                else:
                    ret.set_result(True)

        def release_waiter():
            nonlocal ret
            if not ret.done():
                ret.set_result(False)

        for f in fs:
            f.add_done_callback(waiter)
        self.run_delayed(timeout or 0, release_waiter)

        return ret

    async def asleep(self, delay):
        deadline = time.time() + delay
        await reschedule(deadline)

    async def _cancel_and_wait(self, fut):
        waiter = Future(self)
        fut.chain(waiter)
        fut.cancel()
        await waiter
        try:
            return fut.result()
        except concurrent.futures.CancelledError as exc:
            raise concurrent.futures.TimeoutError() from exc

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
                # Let _Task.step do its thing
                continue
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

        if not self.is_alive():
            # self.run() is called directly
            self.running = True
        else:
            # self.run() is called in a dedicated thread
            # Before running our event loop we must ensure that our parent thread has already
            # started. This is necessary for example when 4 threads A, B, C and D are starting
            # concurrently with A calling B.start(), C calling D.start() while B is the parent
            # thread of D.
            parent_thread_grace_period = 1.0
            deadline = time.time() + parent_thread_grace_period
            while not self.parent.is_alive():
                time.sleep(0.005)
                if deadline < time.time():
                    self.running = False
                    self.logger.error("Parent thread failed to start")

        _set_running_loop(self)
        assert get_running_loop() is self

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

    def _dump_traceback(self):
        with TemporaryFile() as f:
            faulthandler.dump_traceback(file=f)
            f.seek(0)
            trace = f.read()
            self.logger.warning(trace)

    def enable_watchdog(self, delay_ms, callback=None):
        if self._watchdog_user_cb is not None:
            self.logger.warning("Event loop watchdog already enabled")
            return
        if callback is None:
            callback = self._dump_traceback
        self._watchdog_user_cb = callback
        od.pomp_loop_watchdog_enable(
            self.pomp_loop, delay_ms, self._watchdog_cb_imp, None
        )

    def disable_watchdog(self):
        if self._watchdog_user_cb is None:
            self.logger.warning("Event loop watchdog already disabled")
            return
        od.pomp_loop_watchdog_disable(self.pomp_loop)
        self._watchdog_user_cb = None

    @callback_decorator()
    def _watchdog_cb(self):
        if self._watchdog_user_cb is None:
            self.logger.error(f"Event loop {self!r} watchdog has no callback")
            self._dump_traceback()
            return
        self.logger.error(f"Event loop {self!r} watchdog triggered")
        self._watchdog_user_cb()

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
        if not pomp_timer:
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

        res = od.pomp_timer_clear(pomp_timer)
        if res != 0:
            self.logger.error(f"Error while clearing pomp loop timer: {res}")
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
        if fn in self.cleanup_functions:
            # Do not register the same cleanup functions twice
            self.unregister_cleanup(fn)
        if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
            task = _Task(self, True, fn)
            self.cleanup_functions[fn] = task.step
        else:
            self.cleanup_functions[fn] = fn

    def unregister_cleanup(self, fn, ignore_error=False):
        try:
            func = self.cleanup_functions.pop(fn)
            # async cleanup functions need to be properly cancelled if they've not been run
            obj = getattr(func, "__self__", None)
            if isinstance(obj, _Task) and obj not in current_tasks(self):
                obj.cancel()
        except KeyError:
            if not ignore_error:
                self.logger.error(f"Failed to unregister cleanup function '{fn}'")

    def _collect_futures(self):
        self.futures = set(filter(lambda f: f.running(), self.futures))

    def _cleanup(self):
        # Execute cleanup functions
        for cleanup in reversed(list(self.cleanup_functions.values())):
            try:
                cleanup()
            except Exception:
                self.logger.exception("Unhandled exception in cleanup function")
            try:
                self._wait_and_process()
            except RuntimeError as e:
                self.logger.error(f"Exception caught: {e}")
            self._run_task_list(self.async_pomp_task)
            self._run_task_list(self.deferred_pomp_task)
        for cleanup_fn in reversed(list(self.cleanup_functions.keys())):
            # unregister self registering cleanup functions.
            self.unregister_cleanup(cleanup_fn, ignore_error=True)
        self.cleanup_functions = dict()

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
        self.futures = set()
        self.async_cleanup_running = False

    def _register_future(self, f):
        self.futures.add(f)

    def _unregister_future(self, f, ignore_error=False):
        try:
            self.futures.remove(f)
        except KeyError:
            if not self.async_cleanup_running and not ignore_error:
                self.logger.error(f"Failed to unregister future '{f}'")


async def asleep(delay):
    await get_running_loop().asleep(delay)


async def exit_loop():
    get_running_loop().stop()


__all__ = [
    "get_running_loop",
    "BoundedSemaphore",
    "Condition",
    "Event",
    "Future",
    "Loop",
    "Lock",
    "PompEvent",
    "Semaphore",
    "asleep",
]
