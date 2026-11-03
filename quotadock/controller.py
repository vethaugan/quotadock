"""Background refreshes, bounded retries, and honest stale-data handling."""
from dataclasses import replace
import time
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .models import Snapshot
from .providers import PROVIDERS, ProviderError


class ResultSignals(QObject):
    ready = Signal(str, object, int)


class FetchJob(QRunnable):
    def __init__(self, key):
        super().__init__()
        self.key = key
        self.signals = ResultSignals()

    def run(self):
        provider, product, fetch = PROVIDERS[self.key]
        try:
            result = fetch()
            delay = 300
        except ProviderError as error:
            result = Snapshot(provider, product, status=error.status, detail=error.detail)
            delay = error.retry_after
        except Exception:
            # Never show/log an exception that might contain credentials or a body.
            result = Snapshot(provider, product, status="Unavailable", detail="Could not read quota. Check your CLI setup and try again.")
            delay = 300
        self.signals.ready.emit(self.key, result, delay)


def preserve_last_success(previous, result):
    if result.fetched_at is None and previous and previous.quotas:
        return replace(result, quotas=previous.quotas, fetched_at=previous.fetched_at, stale=True)
    return result


class RefreshController(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.readings = {key: Snapshot(provider, product, status="Connecting…") for key, (provider, product, _) in PROVIDERS.items()}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.jobs = {}
        self.next_attempt = {}
        self.last_attempt = {}

    def refresh(self, manual=False):
        now = time.monotonic()
        for key in PROVIDERS:
            if key in self.jobs or now < self.last_attempt.get(key, -100)+30:
                continue
            # Manual refresh may skip regular cadence, but never a 429 cooldown.
            limited = self.readings[key].status == "Rate limited"
            if now < self.next_attempt.get(key, 0) and (not manual or limited):
                continue
            job = FetchJob(key)
            job.signals.ready.connect(self.receive)
            self.jobs[key] = job
            self.last_attempt[key] = now
            self.pool.start(job)
        self.changed.emit()

    def receive(self, key, result, delay):
        self.readings[key] = preserve_last_success(self.readings.get(key), result)
        self.next_attempt[key] = time.monotonic()+delay
        self.jobs.pop(key, None)
        self.changed.emit()
