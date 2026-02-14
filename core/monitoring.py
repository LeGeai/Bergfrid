"""Health monitoring for publisher platforms."""

import logging
from typing import Dict

log = logging.getLogger("bergfrid.monitoring")


class HealthMonitor:
    """Tracks consecutive failures per platform and triggers alerts."""

    def __init__(self, alert_threshold: int = 5):
        self.alert_threshold = alert_threshold
        self._consecutive_failures: Dict[str, int] = {}
        self._alerted: Dict[str, bool] = {}

    def record_success(self, platform: str) -> None:
        prev = self._consecutive_failures.get(platform, 0)
        if prev > 0:
            log.info("%s: reprise apres %d echec(s) consecutif(s).", platform, prev)
        self._consecutive_failures[platform] = 0
        self._alerted[platform] = False

    def record_failure(self, platform: str) -> bool:
        """Record a failure. Returns True if alert threshold was just crossed."""
        count = self._consecutive_failures.get(platform, 0) + 1
        self._consecutive_failures[platform] = count
        log.warning("%s: echec #%d consecutif.", platform, count)

        if count >= self.alert_threshold and not self._alerted.get(platform, False):
            self._alerted[platform] = True
            log.error(
                "ALERTE: %s a echoue %d fois consecutivement!",
                platform, count,
            )
            return True
        return False

    def get_failures(self, platform: str) -> int:
        return self._consecutive_failures.get(platform, 0)

    def get_status(self) -> Dict[str, int]:
        return dict(self._consecutive_failures)
