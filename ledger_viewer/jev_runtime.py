"""Process-wide concurrency budget shared by optional Jev features."""
from threading import BoundedSemaphore

MODEL = 'jev-1.14.0'
REQUEST_SLOTS = BoundedSemaphore(4)
