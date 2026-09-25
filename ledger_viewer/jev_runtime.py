"""Process-wide concurrency budget shared by optional Jev features."""
from threading import BoundedSemaphore

REQUEST_SLOTS = BoundedSemaphore(4)
