import pytest
from core.monitoring import HealthMonitor


@pytest.fixture
def monitor():
    return HealthMonitor(alert_threshold=3)


class TestHealthMonitor:
    def test_initial_status_empty(self, monitor):
        assert monitor.get_status() == {}

    def test_record_success_resets_counter(self, monitor):
        monitor.record_failure("discord")
        monitor.record_failure("discord")
        monitor.record_success("discord")
        assert monitor.get_failures("discord") == 0

    def test_record_failure_increments(self, monitor):
        monitor.record_failure("telegram")
        monitor.record_failure("telegram")
        assert monitor.get_failures("telegram") == 2

    def test_alert_triggered_at_threshold(self, monitor):
        assert not monitor.record_failure("discord")  # 1
        assert not monitor.record_failure("discord")  # 2
        assert monitor.record_failure("discord")       # 3 = threshold

    def test_alert_only_once(self, monitor):
        monitor.record_failure("discord")  # 1
        monitor.record_failure("discord")  # 2
        assert monitor.record_failure("discord")       # 3 = alert
        assert not monitor.record_failure("discord")   # 4 = no re-alert

    def test_alert_resets_after_success(self, monitor):
        for _ in range(3):
            monitor.record_failure("discord")
        monitor.record_success("discord")
        # Now fail again - should alert again at threshold
        monitor.record_failure("discord")  # 1
        monitor.record_failure("discord")  # 2
        assert monitor.record_failure("discord")  # 3 = alert again

    def test_independent_platforms(self, monitor):
        monitor.record_failure("discord")
        monitor.record_failure("telegram")
        assert monitor.get_failures("discord") == 1
        assert monitor.get_failures("telegram") == 1

    def test_get_failures_unknown_platform(self, monitor):
        assert monitor.get_failures("unknown") == 0

    def test_get_status(self, monitor):
        monitor.record_failure("discord")
        monitor.record_failure("discord")
        monitor.record_failure("telegram")
        status = monitor.get_status()
        assert status == {"discord": 2, "telegram": 1}
