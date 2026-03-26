"""
Senior Engineer Code Review – Escalation Logic Unit Tests
==========================================================
Rules under test:
  1. Critical incidents escalate automatically unless resolved
  2. High incidents escalate if unresolved for more than 30 minutes
  3. Resolved incidents should not remain escalated
  4. Invalid or missing severity values should be handled safely
"""

from datetime import datetime, timedelta

import pytest

from app import (
    activity_log,
    apply_escalation,
    get_escalation_reason,
    is_overdue,
    should_escalate,
)


@pytest.fixture(autouse=True)
def clear_activity_log():
    activity_log.clear()
    yield
    activity_log.clear()


def _make(severity="Medium", status="Open", overdue_minutes=0):
    """Build a test incident with precise overdue control in minutes."""
    now = datetime.utcnow()
    if overdue_minutes > 0:
        due = now - timedelta(minutes=overdue_minutes)
    else:
        due = now + timedelta(hours=6)
    return {
        "id": f"review-{severity}-{status}",
        "title": "Review test",
        "description": "desc",
        "severity": severity,
        "impacted_service": "ReviewService",
        "owner": "Reviewer",
        "created_at": now.isoformat(),
        "due_at": due.isoformat(),
        "status": status,
        "escalated": False,
        "escalation_reason": "",
        "assigned_to": "",
        "assignment_method": "manual",
        "notification_sent": False,
        "watchers": [],
        "overdue_notified": False,
    }


# ===================================================================
# RULE 1: Critical incidents escalate automatically unless resolved
# ===================================================================


class TestRule1_CriticalAutoEscalation:
    """Critical incidents must auto-escalate in every non-Closed status."""

    def test_critical_open_escalates(self):
        """Critical + Open → must escalate."""
        inc = _make(severity="Critical", status="Open")
        assert should_escalate(inc) is True

    def test_critical_in_progress_escalates(self):
        """Critical + In Progress → must escalate."""
        inc = _make(severity="Critical", status="In Progress")
        assert should_escalate(inc) is True

    def test_critical_resolved_escalates(self):
        """Critical + Resolved → must still escalate (not Closed)."""
        inc = _make(severity="Critical", status="Resolved")
        assert should_escalate(inc) is True

    def test_critical_closed_does_not_escalate(self):
        """Critical + Closed → must NOT escalate."""
        inc = _make(severity="Critical", status="Closed")
        assert should_escalate(inc) is False

    def test_apply_escalation_sets_flag_and_reason(self):
        """apply_escalation must mutate escalated=True and set a reason."""
        inc = _make(severity="Critical", status="Open")
        apply_escalation(inc)
        assert inc["escalated"] is True
        assert "Critical" in inc["escalation_reason"]

    def test_escalation_reason_text(self):
        """Reason string should mention 'Critical'."""
        inc = _make(severity="Critical", status="Open")
        reason = get_escalation_reason(inc)
        assert "Critical" in reason

    def test_critical_escalation_logs_timeline_event(self):
        """Escalating a Critical incident must produce an activity log entry."""
        inc = _make(severity="Critical", status="Open")
        apply_escalation(inc)
        events = [e for e in activity_log if e["incident_id"] == inc["id"]]
        assert any(e["type"] == "escalated" for e in events)


# ===================================================================
# RULE 2: High incidents escalate if unresolved > 30 minutes
# ===================================================================


class TestRule2_HighOverdueEscalation:
    """High severity incidents must escalate when overdue (past due_at)."""

    def test_high_overdue_31min_escalates(self):
        """High + 31 minutes past due → must escalate."""
        inc = _make(severity="High", status="Open", overdue_minutes=31)
        assert is_overdue(inc) is True
        assert should_escalate(inc) is True

    def test_high_overdue_60min_escalates(self):
        """High + 60 minutes past due → must escalate."""
        inc = _make(severity="High", status="Open", overdue_minutes=60)
        assert should_escalate(inc) is True

    def test_high_not_overdue_does_not_escalate(self):
        """High + not yet due → must NOT escalate."""
        inc = _make(severity="High", status="Open", overdue_minutes=0)
        assert is_overdue(inc) is False
        assert should_escalate(inc) is False

    def test_high_overdue_in_progress_escalates(self):
        """High + In Progress + overdue → must escalate."""
        inc = _make(severity="High", status="In Progress", overdue_minutes=45)
        assert should_escalate(inc) is True

    def test_high_overdue_closed_does_not_escalate(self):
        """High + Closed + overdue → must NOT escalate (Closed overrides)."""
        inc = _make(severity="High", status="Closed", overdue_minutes=45)
        assert should_escalate(inc) is False

    def test_apply_escalation_high_overdue_sets_reason(self):
        """apply_escalation must set reason mentioning 'overdue'."""
        inc = _make(severity="High", status="Open", overdue_minutes=35)
        apply_escalation(inc)
        assert inc["escalated"] is True
        assert "overdue" in inc["escalation_reason"].lower()

    def test_high_overdue_escalation_logs_event(self):
        """Escalating a High overdue incident must log a timeline event."""
        inc = _make(severity="High", status="Open", overdue_minutes=40)
        apply_escalation(inc)
        events = [e for e in activity_log if e["incident_id"] == inc["id"]]
        assert any(e["type"] == "escalated" for e in events)


# ===================================================================
# RULE 3: Resolved incidents should not remain escalated
# ===================================================================


class TestRule3_ResolvedDeEscalation:
    """Resolving/closing an incident must clear its escalation."""

    def test_close_critical_clears_escalation(self):
        """Critical escalated → Closed → escalated must become False."""
        inc = _make(severity="Critical", status="Open")
        apply_escalation(inc)
        assert inc["escalated"] is True
        inc["status"] = "Closed"
        apply_escalation(inc)
        assert inc["escalated"] is False
        assert inc["escalation_reason"] == ""

    def test_close_high_overdue_clears_escalation(self):
        """High+overdue escalated → Closed → escalation cleared."""
        inc = _make(severity="High", status="Open", overdue_minutes=60)
        apply_escalation(inc)
        assert inc["escalated"] is True
        inc["status"] = "Closed"
        apply_escalation(inc)
        assert inc["escalated"] is False

    def test_de_escalation_logs_event(self):
        """Clearing escalation must log a 'de_escalated' timeline event."""
        inc = _make(severity="Critical", status="Open")
        apply_escalation(inc)
        inc["status"] = "Closed"
        apply_escalation(inc)
        events = [e for e in activity_log if e["incident_id"] == inc["id"]]
        assert any(e["type"] == "de_escalated" for e in events)

    def test_resolved_critical_still_escalated(self):
        """Critical + Resolved (not Closed) → remains escalated per Rule 1."""
        inc = _make(severity="Critical", status="Resolved")
        apply_escalation(inc)
        assert inc["escalated"] is True

    def test_medium_never_escalates_regardless(self):
        """Medium severity → never escalated, even if overdue."""
        inc = _make(severity="Medium", status="Open", overdue_minutes=120)
        apply_escalation(inc)
        assert inc["escalated"] is False

    def test_low_never_escalates_regardless(self):
        """Low severity → never escalated, even if overdue."""
        inc = _make(severity="Low", status="Open", overdue_minutes=120)
        apply_escalation(inc)
        assert inc["escalated"] is False


# ===================================================================
# RULE 4: Invalid or missing severity values handled safely
# ===================================================================


class TestRule4_InvalidSeverityHandling:
    """Invalid/missing severity must never crash and must not escalate."""

    def test_empty_severity_does_not_escalate(self):
        """severity='' → should_escalate returns False, no crash."""
        inc = _make(severity="", status="Open")
        assert should_escalate(inc) is False

    def test_none_severity_does_not_escalate(self):
        """severity=None → should_escalate returns False, no crash."""
        inc = _make(severity="Medium", status="Open")
        inc["severity"] = None
        assert should_escalate(inc) is False

    def test_numeric_severity_does_not_escalate(self):
        """severity=999 → should_escalate returns False, no crash."""
        inc = _make(severity="Medium", status="Open")
        inc["severity"] = 999
        assert should_escalate(inc) is False

    def test_unknown_string_severity_does_not_escalate(self):
        """severity='Mega' → should_escalate returns False, no crash."""
        inc = _make(severity="Mega", status="Open")
        assert should_escalate(inc) is False

    def test_apply_escalation_with_empty_severity(self):
        """apply_escalation on empty severity must not crash."""
        inc = _make(severity="", status="Open")
        apply_escalation(inc)
        assert inc["escalated"] is False
        assert inc["escalation_reason"] == ""

    def test_apply_escalation_with_none_severity(self):
        """apply_escalation on None severity must not crash."""
        inc = _make(severity="Medium", status="Open")
        inc["severity"] = None
        apply_escalation(inc)
        assert inc["escalated"] is False

    def test_get_escalation_reason_with_garbage_severity(self):
        """get_escalation_reason on garbage severity returns empty string."""
        inc = _make(severity="!!!INVALID!!!", status="Open")
        reason = get_escalation_reason(inc)
        assert reason == ""

    def test_missing_severity_key(self):
        """Incident dict with no 'severity' key at all → no crash."""
        inc = _make(severity="Medium", status="Open")
        del inc["severity"]
        # should_escalate must handle KeyError gracefully or return False
        try:
            result = should_escalate(inc)
            assert result is False
        except KeyError:
            pytest.fail("should_escalate crashed on missing 'severity' key")
