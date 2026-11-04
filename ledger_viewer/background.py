"""Cancellable local work with Tk callbacks confined to the owning thread."""

from queue import Empty, Queue
from threading import BoundedSemaphore, Event, Thread
import time


_slots = BoundedSemaphore(2)


class Cancelled(Exception):
    pass


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise Cancelled()


def cooperate(cancelled):
    """Give Tk a short execution window during CPU-heavy local parsing."""
    check_cancelled(cancelled)
    time.sleep(0.002)


class BackgroundTasks:
    def __init__(self, owner):
        self.owner = owner
        self.active = {}
        self.queue = Queue()
        self.poll_id = None
        self.closed = False
        owner.bind("<Destroy>", self._destroy, add="+")

    def submit(self, key, work, done, error=None, progress=None):
        self.cancel(key)
        cancelled = Event()
        self.active[key] = (cancelled, done, error, progress)
        queue = self.queue

        def run():
            acquired = False
            try:
                while not acquired:
                    check_cancelled(cancelled)
                    acquired = _slots.acquire(timeout=0.05)
                check_cancelled(cancelled)
                result = work(cancelled, lambda value: queue.put((key, cancelled, "progress", value)))
                check_cancelled(cancelled)
                queue.put((key, cancelled, "done", result))
            except Exception as exc:
                queue.put((key, cancelled, "error", exc))
            finally:
                if acquired:
                    _slots.release()

        Thread(target=run, daemon=True).start()
        if self.poll_id is None:
            self.poll_id = self.owner.after(25, self._poll)

    def cancel(self, key):
        task = self.active.pop(key, None)
        if task:
            task[0].set()

    def cancel_all(self):
        for key in tuple(self.active):
            self.cancel(key)

    def busy(self, key):
        return key in self.active

    def _poll(self):
        self.poll_id = None
        updates = {}
        completions = []
        try:
            for _ in range(200):
                key, cancelled, kind, value = self.queue.get_nowait()
                task = self.active.get(key)
                if not task or task[0] is not cancelled or cancelled.is_set():
                    continue
                if kind == "progress":
                    updates[key] = (cancelled, value)
                else:
                    completions.append((key, cancelled, kind, value))
        except Empty:
            pass
        for key, (cancelled, value) in updates.items():
            task = self.active.get(key)
            if task and task[0] is cancelled and task[3]:
                task[3](value)
        for key, cancelled, kind, value in completions:
            task = self.active.get(key)
            if not task or task[0] is not cancelled or cancelled.is_set():
                continue
            self.active.pop(key, None)
            callback = task[1] if kind == "done" else task[2]
            if callback:
                try:
                    callback(value)
                except Exception as exc:
                    self.owner._root().report_callback_exception(type(exc), exc, exc.__traceback__)
        if self.active and not self.closed:
            self.poll_id = self.owner.after(25, self._poll)

    def _destroy(self, event):
        if event.widget is self.owner:
            self.closed = True
            self.cancel_all()
            if self.poll_id is not None:
                self.owner.after_cancel(self.poll_id)
                self.poll_id = None
