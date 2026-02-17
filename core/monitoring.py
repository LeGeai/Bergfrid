"""Health monitoring for publisher platforms."""

import logging
import time
from typing import Dict

log = logging.getLogger("bergfrid.monitoring")


class HealthMonitor:
    """Tracks consecutive failures per platform and triggers alerts."""

    def __init__(self, alert_threshold: int = 5, cooldown_max_minutes: float = 60):
        self.alert_threshold = alert_threshold
        self.cooldown_max_minutes = cooldown_max_minutes
        self._consecutive_failures: Dict[str, int] = {}
        self._alerted: Dict[str, bool] = {}
        self._last_attempt_time: Dict[str, float] = {}

    def record_success(self, platform: str) -> None:
        prev = self._consecutive_failures.get(platform, 0)
        if prev > 0:
            log.info("%s: reprise apres %d echec(s) consecutif(s).", platform, prev)
        self._consecutive_failures[platform] = 0
        self._alerted[platform] = False
        self._last_attempt_time.pop(platform, None)

    def record_failure(self, platform: str) -> bool:
        """Record a failure. Returns True if alert threshold was just crossed."""
        count = self._consecutive_failures.get(platform, 0) + 1
        self._consecutive_failures[platform] = count
        self._last_attempt_time[platform] = time.monotonic()
        log.warning("%s: echec #%d consecutif.", platform, count)

        if count >= self.alert_threshold and not self._alerted.get(platform, False):
            self._alerted[platform] = True
            log.error(
                "ALERTE: %s a echoue %d fois consecutivement!",
                platform, count,
            )
            return True
        return False

    def is_in_cooldown(self, platform: str) -> bool:
        """Check if platform should be skipped this tick (progressive cooldown)."""
        failures = self._consecutive_failures.get(platform, 0)
        if failures < self.alert_threshold:
            return False
        last = self._last_attempt_time.get(platform)
        if last is None:
            return False
        # Cooldown progressif: echecs * 2 min, plafond cooldown_max_minutes
        cooldown_sec = min(failures * 120, self.cooldown_max_minutes * 60)
        elapsed = time.monotonic() - last
        if elapsed < cooldown_sec:
            remaining = (cooldown_sec - elapsed) / 60
            log.info(
                "%s: cooldown actif (encore %.0f min, apres %d echecs). Skip.",
                platform, remaining, failures,
            )
            return True
        return False

    def get_failures(self, platform: str) -> int:
        return self._consecutive_failures.get(platform, 0)

    def get_status(self) -> Dict[str, int]:
        return dict(self._consecutive_failures)
