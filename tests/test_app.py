import json
from datetime import datetime, timedelta

import pytest

from app import (
    activity_log,
    add_event,
    app,
    apply_escalation,
    auto_assign,
    check_overdue_warnings,
    get_escalation_reason,
    get_timeline,
    incidents,
    is_overdue,
    notification_log,
    notify_stakeholders,
    on_call_roster,
    send_notification,
    should_escalate,
    MAX_ACTIVITY_LOG,
    MAX_NOTIFICATION_LOG,
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
        "assigned_to": "",
        "assignment_method": "manual",
        "notification_sent": False,
        "watchers": [],
        "overdue_notified": False,
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


class TestTimeline:
    """Activity timeline / event log tests."""

    @pytest.fixture(autouse=True)
    def clear_log(self):
        activity_log.clear()
        yield
        activity_log.clear()

    def test_add_event_creates_entry(self):
        ev = add_event("inc-1", "created", "Incident created")
        assert ev["incident_id"] == "inc-1"
        assert ev["type"] == "created"
        assert ev["message"] == "Incident created"
        assert len(activity_log) == 1

    def test_get_timeline_returns_newest_first(self):
        add_event("inc-1", "created", "First")
        add_event("inc-1", "status_change", "Second")
        timeline = get_timeline("inc-1")
        assert len(timeline) == 2
        assert timeline[0]["message"] == "Second"
        assert timeline[1]["message"] == "First"

    def test_get_timeline_filters_by_incident(self):
        add_event("inc-1", "created", "A")
        add_event("inc-2", "created", "B")
        assert len(get_timeline("inc-1")) == 1
        assert len(get_timeline("inc-2")) == 1

    def test_log_eviction_at_max(self):
        for i in range(MAX_ACTIVITY_LOG + 50):
            add_event("inc-1", "created", f"Event {i}")
        assert len(activity_log) == MAX_ACTIVITY_LOG

    def test_creation_route_logs_event(self, client):
        resp = client.post(
            "/incidents",
            json={
                "title": "Timeline test",
                "description": "desc",
                "severity": "Low",
                "impacted_service": "svc",
                "owner": "owner",
                "due_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 201
        inc_id = resp.get_json()["id"]
        timeline = get_timeline(inc_id)
        assert any(e["type"] == "created" for e in timeline)

    def test_status_change_logs_event(self, client):
        target = incidents[0]
        old_status = target["status"]
        new_status = "Resolved" if old_status != "Resolved" else "In Progress"
        client.post(f"/incidents/{target['id']}/status", json={"status": new_status})
        timeline = get_timeline(target["id"])
        assert any(e["type"] == "status_change" for e in timeline)

    def test_escalation_logs_event(self):
        inc = _make_incident(severity="Critical", status="Open")
        inc["id"] = "esc-test"
        apply_escalation(inc)
        timeline = get_timeline("esc-test")
        assert any(e["type"] == "escalated" for e in timeline)

    def test_de_escalation_logs_event(self):
        inc = _make_incident(severity="Critical", status="Open")
        inc["id"] = "de-esc-test"
        apply_escalation(inc)
        inc["status"] = "Closed"
        apply_escalation(inc)
        timeline = get_timeline("de-esc-test")
        assert any(e["type"] == "de_escalated" for e in timeline)

    def test_timeline_api_route(self, client):
        add_event("api-test", "created", "Test event")
        resp = client.get("/timeline/api-test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["message"] == "Test event"

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


# ---------------------------------------------------------------------------
# Auto-assignment tests
# ---------------------------------------------------------------------------


class TestAutoAssign:
    """auto_assign matches impacted_service to on-call roster."""

    @pytest.fixture(autouse=True)
    def clear_logs(self):
        activity_log.clear()
        notification_log.clear()
        yield
        activity_log.clear()
        notification_log.clear()

    def test_assigns_matching_on_call_member(self):
        inc = _make_incident()
        inc["id"] = "aa-1"
        inc["impacted_service"] = "Payment Service"
        result = auto_assign(inc)
        assert result == "Alice"
        assert inc["assigned_to"] == "Alice"
        assert inc["assignment_method"] == "auto"

    def test_no_match_returns_empty(self):
        inc = _make_incident()
        inc["id"] = "aa-2"
        inc["impacted_service"] = "Unknown Service"
        result = auto_assign(inc)
        assert result == ""
        assert inc["assigned_to"] == ""

    def test_skips_off_call_member(self):
        inc = _make_incident()
        inc["id"] = "aa-3"
        inc["impacted_service"] = "API Gateway"  # Carol is off-call
        result = auto_assign(inc)
        assert result == ""

    def test_logs_assigned_event(self):
        inc = _make_incident()
        inc["id"] = "aa-4"
        inc["impacted_service"] = "Auth Service"
        auto_assign(inc)
        timeline = get_timeline("aa-4")
        assert any(e["type"] == "assigned" for e in timeline)

    def test_sends_notification_on_assign(self):
        inc = _make_incident()
        inc["id"] = "aa-5"
        inc["impacted_service"] = "Payment Service"
        auto_assign(inc)
        notifs = [n for n in notification_log if n["incident_id"] == "aa-5"]
        assert len(notifs) >= 1
        assert notifs[0]["type"] == "assignment"
        assert notifs[0]["recipient"] == "Alice"


# ---------------------------------------------------------------------------
# Notification tests
# ---------------------------------------------------------------------------


class TestNotifications:
    """send_notification and notify_stakeholders tests."""

    @pytest.fixture(autouse=True)
    def clear_logs(self):
        activity_log.clear()
        notification_log.clear()
        yield
        activity_log.clear()
        notification_log.clear()

    def test_send_notification_records_entry(self):
        n = send_notification("inc-1", "Alice", "in_app", "assignment", "Test msg")
        assert n["recipient"] == "Alice"
        assert n["read"] is False
        assert len(notification_log) == 1

    def test_send_notification_logs_event(self):
        send_notification("inc-1", "Bob", "in_app", "escalation", "Esc msg")
        timeline = get_timeline("inc-1")
        assert any(e["type"] == "notified" for e in timeline)

    def test_notify_stakeholders_fans_out(self):
        inc = _make_incident()
        inc["id"] = "ns-1"
        inc["assigned_to"] = "Alice"
        inc["owner"] = "Bob"
        inc["watchers"] = ["Carol"]
        sent = notify_stakeholders(inc, "status_change", "Status updated")
        recipients = {n["recipient"] for n in sent}
        assert recipients == {"Alice", "Bob", "Carol"}

    def test_notify_stakeholders_deduplicates(self):
        inc = _make_incident()
        inc["id"] = "ns-2"
        inc["assigned_to"] = "Alice"
        inc["owner"] = "Alice"  # same person
        inc["watchers"] = ["Alice"]
        sent = notify_stakeholders(inc, "status_change", "Test")
        assert len(sent) == 1

    def test_notification_log_eviction(self):
        for i in range(MAX_NOTIFICATION_LOG + 50):
            send_notification("inc-1", "X", "in_app", "test", f"Msg {i}")
        assert len(notification_log) == MAX_NOTIFICATION_LOG


# ---------------------------------------------------------------------------
# Overdue warning tests
# ---------------------------------------------------------------------------


class TestOverdueWarnings:
    """check_overdue_warnings scans incidents and notifies."""

    @pytest.fixture(autouse=True)
    def clear_logs(self):
        activity_log.clear()
        notification_log.clear()
        yield
        activity_log.clear()
        notification_log.clear()

    def test_sends_warning_for_overdue_incident(self):
        inc = _make_incident(severity="High", overdue=True)
        inc["id"] = "ow-1"
        inc["assigned_to"] = "Bob"
        incidents.append(inc)
        try:
            sent = check_overdue_warnings()
            assert len(sent) >= 1
            assert sent[0]["type"] == "overdue_warning"
            assert inc["overdue_notified"] is True
        finally:
            incidents.remove(inc)

    def test_no_duplicate_warning(self):
        inc = _make_incident(severity="High", overdue=True)
        inc["id"] = "ow-2"
        inc["assigned_to"] = "Bob"
        incidents.append(inc)
        try:
            check_overdue_warnings()
            count_before = len(notification_log)
            check_overdue_warnings()  # second call
            assert len(notification_log) == count_before
        finally:
            incidents.remove(inc)

    def test_skips_closed_incidents(self):
        inc = _make_incident(severity="High", status="Closed", overdue=True)
        inc["id"] = "ow-3"
        inc["assigned_to"] = "Bob"
        incidents.append(inc)
        try:
            sent = check_overdue_warnings()
            assert all(n["incident_id"] != "ow-3" for n in sent)
        finally:
            incidents.remove(inc)

    def test_logs_overdue_warning_event(self):
        inc = _make_incident(severity="High", overdue=True)
        inc["id"] = "ow-4"
        inc["assigned_to"] = "Bob"
        incidents.append(inc)
        try:
            check_overdue_warnings()
            timeline = get_timeline("ow-4")
            assert any(e["type"] == "overdue_warning" for e in timeline)
        finally:
            incidents.remove(inc)


# ---------------------------------------------------------------------------
# Roster & assignment route tests
# ---------------------------------------------------------------------------


class TestRosterRoutes:
    """CRUD routes for on_call_roster."""

    def test_list_roster(self, client):
        resp = client.get("/roster")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_add_roster_member(self, client):
        resp = client.post("/roster", json={
            "name": "Zara", "email": "zara@example.com", "team": "QA", "on_call": True
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Zara"
        # Cleanup
        on_call_roster[:] = [m for m in on_call_roster if m["name"] != "Zara"]

    def test_add_roster_validation(self, client):
        resp = client.post("/roster", json={"name": "X"})
        assert resp.status_code == 400

    def test_update_roster_member(self, client):
        target = on_call_roster[0]
        resp = client.put(f"/roster/{target['id']}", json={"on_call": False})
        assert resp.status_code == 200
        assert resp.get_json()["on_call"] is False
        target["on_call"] = True  # restore

    def test_delete_roster_member(self, client):
        saved = on_call_roster[:]
        resp = client.delete(f"/roster/{on_call_roster[-1]['id']}")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] is True
        on_call_roster[:] = saved  # restore

    def test_assign_incident(self, client):
        target = incidents[0]
        resp = client.post(f"/incidents/{target['id']}/assign",
                           json={"assigned_to": "NewPerson"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["assigned_to"] == "NewPerson"
        assert data["assignment_method"] == "manual"

    def test_assign_missing_name_rejected(self, client):
        resp = client.post(f"/incidents/{incidents[0]['id']}/assign", json={})
        assert resp.status_code == 400


class TestNotificationRoutes:
    """Notification API routes."""

    @pytest.fixture(autouse=True)
    def seed_notification(self):
        notification_log.clear()
        send_notification("route-test", "Alice", "in_app", "test", "Hello")
        yield
        notification_log.clear()
        activity_log.clear()

    def test_list_notifications(self, client):
        resp = client.get("/notifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1

    def test_list_unread_only(self, client):
        resp = client.get("/notifications?unread=true")
        assert resp.status_code == 200
        data = resp.get_json()
        assert all(not n["read"] for n in data)

    def test_incident_notifications(self, client):
        resp = client.get("/incidents/route-test/notifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

    def test_mark_notification_read(self, client):
        notif = notification_log[0]
        resp = client.post(f"/notifications/{notif['id']}/read")
        assert resp.status_code == 200
        assert resp.get_json()["read"] is True

    def test_mark_unknown_notification_404(self, client):
        resp = client.post("/notifications/nonexistent/read")
        assert resp.status_code == 404


class TestAutoAssignOnCreate:
    """Creating a Critical incident for a known service auto-assigns and notifies."""

    @pytest.fixture(autouse=True)
    def clear_logs(self):
        activity_log.clear()
        notification_log.clear()
        yield
        activity_log.clear()
        notification_log.clear()

    def test_critical_incident_auto_assigned(self, client):
        resp = client.post("/incidents", json={
            "title": "Critical DB outage",
            "description": "Everything is down",
            "severity": "Critical",
            "impacted_service": "Payment Service",
            "owner": "Reporter",
            "due_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["assigned_to"] == "Alice"
        assert data["assignment_method"] == "auto"
        # Cleanup
        incidents[:] = [i for i in incidents if i["id"] != data["id"]]

    def test_critical_incident_escalation_notifications(self, client):
        resp = client.post("/incidents", json={
            "title": "Critical auth crash",
            "description": "Auth is down",
            "severity": "Critical",
            "impacted_service": "Auth Service",
            "owner": "Reporter",
            "due_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
        })
        assert resp.status_code == 201
        data = resp.get_json()
        # Should have assignment + escalation notifications
        notifs = [n for n in notification_log if n["incident_id"] == data["id"]]
        types = {n["type"] for n in notifs}
        assert "assignment" in types
        assert "escalation" in types
        incidents[:] = [i for i in incidents if i["id"] != data["id"]]
