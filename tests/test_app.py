import json
from datetime import datetime, timedelta

import pytest

from app import (
    app,
    apply_escalation,
    get_escalation_reason,
    incidents,
    is_overdue,
    should_escalate,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helper-function unit tests
# ---------------------------------------------------------------------------


def _make_incident(severity="Medium", status="Open", overdue=False):
    now = datetime.utcnow()
    due = now - timedelta(hours=1) if overdue else now + timedelta(hours=6)
    return {
        "id": "test-id",
        "title": "Test incident",
        "description": "desc",
        "severity": severity,
        "impacted_service": "TestService",
        "owner": "Tester",
        "created_at": now.isoformat(),
        "due_at": due.isoformat(),
        "status": status,
        "escalated": False,
        "escalation_reason": "",
    }


class TestCriticalAutoEscalation:
    """Critical severity incidents must be auto-escalated unless Closed."""

    def test_critical_open_is_escalated(self):
        inc = _make_incident(severity="Critical", status="Open")
        assert should_escalate(inc) is True

    def test_critical_in_progress_is_escalated(self):
        inc = _make_incident(severity="Critical", status="In Progress")
        assert should_escalate(inc) is True

    def test_critical_resolved_is_escalated(self):
        inc = _make_incident(severity="Critical", status="Resolved")
        assert should_escalate(inc) is True

    def test_escalation_reason_mentions_critical(self):
        inc = _make_incident(severity="Critical", status="Open")
        reason = get_escalation_reason(inc)
        assert "Critical" in reason

    def test_apply_escalation_sets_fields(self):
        inc = _make_incident(severity="Critical", status="Open")
        apply_escalation(inc)
        assert inc["escalated"] is True
        assert inc["escalation_reason"] != ""


class TestOverdueHighEscalation:
    """High severity incidents that are overdue must be escalated."""

    def test_high_overdue_is_escalated(self):
        inc = _make_incident(severity="High", status="Open", overdue=True)
        assert should_escalate(inc) is True

    def test_high_not_overdue_not_escalated(self):
        inc = _make_incident(severity="High", status="Open", overdue=False)
        assert should_escalate(inc) is False

    def test_overdue_detection(self):
        inc = _make_incident(severity="High", overdue=True)
        assert is_overdue(inc) is True

    def test_not_overdue(self):
        inc = _make_incident(severity="High", overdue=False)
        assert is_overdue(inc) is False

    def test_escalation_reason_mentions_overdue(self):
        inc = _make_incident(severity="High", status="Open", overdue=True)
        reason = get_escalation_reason(inc)
        assert "overdue" in reason.lower()


class TestClosedNotEscalated:
    """Closed incidents must never be escalated, regardless of severity."""

    def test_critical_closed_not_escalated(self):
        inc = _make_incident(severity="Critical", status="Closed")
        assert should_escalate(inc) is False

    def test_high_overdue_closed_not_escalated(self):
        inc = _make_incident(severity="High", status="Closed", overdue=True)
        assert should_escalate(inc) is False

    def test_apply_escalation_clears_on_close(self):
        inc = _make_incident(severity="Critical", status="Open")
        apply_escalation(inc)
        assert inc["escalated"] is True
        inc["status"] = "Closed"
        apply_escalation(inc)
        assert inc["escalated"] is False
        assert inc["escalation_reason"] == ""


class TestValidation:
    """Creating an incident with missing required fields returns errors."""

    def test_missing_title(self, client):
        resp = client.post(
            "/incidents",
            json={
                "description": "desc",
                "severity": "Low",
                "impacted_service": "svc",
                "owner": "owner",
                "due_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert any("title" in e for e in data["errors"])

    def test_missing_multiple_fields(self, client):
        resp = client.post("/incidents", json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert len(data["errors"]) >= 5  # all required fields missing

    def test_invalid_severity(self, client):
        resp = client.post(
            "/incidents",
            json={
                "title": "t",
                "description": "d",
                "severity": "Mega",
                "impacted_service": "svc",
                "owner": "o",
                "due_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert any("severity" in e.lower() for e in data["errors"])

    def test_valid_creation(self, client):
        resp = client.post(
            "/incidents",
            json={
                "title": "New bug",
                "description": "Something broke",
                "severity": "Medium",
                "impacted_service": "API",
                "owner": "Tester",
                "due_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "New bug"
        assert data["status"] == "Open"


class TestStatusUpdate:
    """Updating status should re-evaluate escalation."""

    def test_close_critical_removes_escalation(self, client):
        # Find a critical non-closed incident from seed data
        critical = [i for i in incidents if i["severity"] == "Critical" and i["status"] != "Closed"]
        assert len(critical) > 0
        target = critical[0]

        resp = client.post(
            f"/incidents/{target['id']}/status",
            json={"status": "Closed"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["escalated"] is False

    def test_invalid_status_rejected(self, client):
        resp = client.post(
            f"/incidents/{incidents[0]['id']}/status",
            json={"status": "Banana"},
        )
        assert resp.status_code == 400


class TestRoutes:
    """Smoke tests for routes."""

    def test_dashboard_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_incidents_json(self, client):
        resp = client.get("/incidents")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_dashboard_with_filters(self, client):
        resp = client.get("/?severity=Critical&status=Open&escalated=true")
        assert resp.status_code == 200
