from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import uuid
import copy
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# ---------------------------------------------------------------------------
# Activity timeline (in-memory event log)
# ---------------------------------------------------------------------------

EVENT_ICONS = {
    "created": "&#128229;",
    "status_change": "&#128221;",
    "escalated": "&#128314;",
    "de_escalated": "&#9989;",
    "overdue": "&#9200;",
    "assigned": "&#128100;",
    "reassigned": "&#128101;",
    "notified": "&#128276;",
    "overdue_warning": "&#9200;",
}

activity_log: list[dict] = []
MAX_ACTIVITY_LOG = 500


def add_event(incident_id: str, event_type: str, message: str) -> dict:
    """Append an event to the activity log and return it."""
    event = {
        "id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "type": event_type,
        "icon": EVENT_ICONS.get(event_type, "&#8505;"),
        "message": message,
        "created_at": datetime.utcnow().isoformat(),
    }
    activity_log.append(event)
    # Evict oldest events when the log grows too large
    if len(activity_log) > MAX_ACTIVITY_LOG:
        del activity_log[:len(activity_log) - MAX_ACTIVITY_LOG]
    return event


def get_timeline(incident_id: str) -> list[dict]:
    """Return events for a specific incident, newest first."""
    return [e for e in reversed(activity_log) if e["incident_id"] == incident_id]


def get_timeline_map() -> dict[str, list[dict]]:
    """Return a dict mapping incident_id -> list of events (newest first)."""
    result: dict[str, list[dict]] = {}
    for e in activity_log:
        result.setdefault(e["incident_id"], []).append(e)
    # Reverse each list so newest is first
    for k in result:
        result[k] = list(reversed(result[k]))
    return result

# ---------------------------------------------------------------------------
# On-call roster (in-memory)
# ---------------------------------------------------------------------------

on_call_roster: list[dict] = [
    {"id": str(uuid.uuid4()), "name": "Alice", "email": "alice@example.com", "team": "Payment Service", "on_call": True},
    {"id": str(uuid.uuid4()), "name": "Bob", "email": "bob@example.com", "team": "Compute Cluster", "on_call": True},
    {"id": str(uuid.uuid4()), "name": "Carol", "email": "carol@example.com", "team": "API Gateway", "on_call": False},
    {"id": str(uuid.uuid4()), "name": "Eve", "email": "eve@example.com", "team": "Auth Service", "on_call": True},
    {"id": str(uuid.uuid4()), "name": "Frank", "email": "frank@example.com", "team": "Logging Infrastructure", "on_call": True},
]

# ---------------------------------------------------------------------------
# Notification log (in-memory)
# ---------------------------------------------------------------------------

notification_log: list[dict] = []
MAX_NOTIFICATION_LOG = 500


def send_notification(incident_id: str, recipient: str, channel: str,
                      notif_type: str, message: str) -> dict:
    """Record a notification and log it to the activity timeline."""
    notification = {
        "id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "recipient": recipient,
        "channel": channel,
        "type": notif_type,
        "message": message,
        "created_at": datetime.utcnow().isoformat(),
        "read": False,
    }
    notification_log.append(notification)
    if len(notification_log) > MAX_NOTIFICATION_LOG:
        del notification_log[:len(notification_log) - MAX_NOTIFICATION_LOG]
    add_event(incident_id, "notified",
             f"Notification sent to {recipient} ({notif_type}, {channel})")
    return notification


def notify_stakeholders(incident: dict, notif_type: str, message: str) -> list[dict]:
    """Fan out a notification to the assignee, reporter, and all watchers."""
    sent = []
    recipients = set()
    assigned = incident.get("assigned_to", "")
    if assigned:
        recipients.add(assigned)
    owner = incident.get("owner", "")
    if owner:
        recipients.add(owner)
    for w in incident.get("watchers", []):
        recipients.add(w)
    for name in recipients:
        sent.append(send_notification(
            incident["id"], name, "in_app", notif_type, message))
    return sent


def auto_assign(incident: dict) -> str:
    """Match impacted_service to on-call roster and assign the incident.

    Returns the assigned person's name, or empty string if no match.
    """
    service = incident.get("impacted_service", "")
    for member in on_call_roster:
        if member["team"] == service and member["on_call"]:
            incident["assigned_to"] = member["name"]
            incident["assignment_method"] = "auto"
            incident["notification_sent"] = True
            add_event(incident["id"], "assigned",
                      f"Auto-assigned to {member['name']} ({service} on-call)")
            send_notification(
                incident["id"], member["name"], "in_app",
                "assignment",
                f"You have been auto-assigned incident: {incident['title']}")
            return member["name"]
    return ""


def check_overdue_warnings() -> list[dict]:
    """Scan all incidents and send overdue warnings for those approaching or past due.

    Only sends one warning per incident (tracked via 'overdue_notified' flag).
    Returns the list of notifications sent.
    """
    sent = []
    for inc in incidents:
        if inc["status"] in ("Closed", "Resolved"):
            continue
        if inc.get("overdue_notified"):
            continue
        if not is_overdue(inc):
            continue
        inc["overdue_notified"] = True
        add_event(inc["id"], "overdue_warning",
                  f"Overdue warning – incident past due")
        assigned = inc.get("assigned_to") or inc.get("owner", "")
        if assigned:
            n = send_notification(
                inc["id"], assigned, "in_app", "overdue_warning",
                f"Incident \"{inc['title']}\" is overdue")
            sent.append(n)
    return sent


# ---------------------------------------------------------------------------
# In-memory data store (swap for DB-backed repository later)
# ---------------------------------------------------------------------------

VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}
VALID_STATUSES = {"Open", "In Progress", "Resolved", "Closed"}

REQUIRED_FIELDS = ["title", "description", "severity", "impacted_service", "owner", "due_at"]


def _seed_incidents():
    now = datetime.utcnow()
    return [
        {
            "id": str(uuid.uuid4()),
            "title": "Database connection pool exhausted",
            "description": "Primary DB connection pool is saturated, causing request timeouts.",
            "severity": "Critical",
            "impacted_service": "Payment Service",
            "owner": "Alice",
            "created_at": (now - timedelta(hours=6)).isoformat(),
            "due_at": (now + timedelta(hours=2)).isoformat(),
            "status": "Open",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "High memory usage on worker nodes",
            "description": "Worker nodes exceeding 90% memory utilisation.",
            "severity": "High",
            "impacted_service": "Compute Cluster",
            "owner": "Bob",
            "created_at": (now - timedelta(days=2)).isoformat(),
            "due_at": (now - timedelta(hours=12)).isoformat(),  # overdue
            "status": "In Progress",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "SSL certificate expiring soon",
            "description": "Certificate for api.example.com expires in 5 days.",
            "severity": "Medium",
            "impacted_service": "API Gateway",
            "owner": "Carol",
            "created_at": (now - timedelta(days=1)).isoformat(),
            "due_at": (now + timedelta(days=5)).isoformat(),
            "status": "Open",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Minor UI alignment issue",
            "description": "Footer links misaligned on mobile viewport.",
            "severity": "Low",
            "impacted_service": "Web Portal",
            "owner": "Dave",
            "created_at": (now - timedelta(days=3)).isoformat(),
            "due_at": (now + timedelta(days=7)).isoformat(),
            "status": "Open",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Auth service returning 503",
            "description": "Intermittent 503 errors from auth micro-service.",
            "severity": "Critical",
            "impacted_service": "Auth Service",
            "owner": "Eve",
            "created_at": (now - timedelta(hours=3)).isoformat(),
            "due_at": (now + timedelta(hours=1)).isoformat(),
            "status": "Resolved",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Disk space warning on log server",
            "description": "Log partition at 85% capacity.",
            "severity": "High",
            "impacted_service": "Logging Infrastructure",
            "owner": "Frank",
            "created_at": (now - timedelta(days=1)).isoformat(),
            "due_at": (now - timedelta(hours=6)).isoformat(),  # overdue
            "status": "Open",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Resolved payment gateway timeout",
            "description": "Previously timed-out payment gateway is now stable.",
            "severity": "Critical",
            "impacted_service": "Payment Service",
            "owner": "Grace",
            "created_at": (now - timedelta(days=4)).isoformat(),
            "due_at": (now - timedelta(days=3)).isoformat(),
            "status": "Closed",
            "escalated": False,
            "escalation_reason": "",
            "assigned_to": "",
            "assignment_method": "manual",
            "notification_sent": False,
            "watchers": [],
            "overdue_notified": False,
        },
    ]


incidents: list[dict] = _seed_incidents()

# ---------------------------------------------------------------------------
# Helper / business-logic functions
# ---------------------------------------------------------------------------


def is_overdue(incident: dict) -> bool:
    """Return True when the incident's due_at is in the past."""
    due = datetime.fromisoformat(incident["due_at"])
    return datetime.utcnow() > due


def should_escalate(incident: dict) -> bool:
    """Determine whether an incident should be escalated."""
    if incident.get("status") == "Closed":
        return False
    severity = incident.get("severity")
    if severity == "Critical":
        return True
    if severity == "High" and is_overdue(incident):
        return True
    return False


def get_escalation_reason(incident: dict) -> str:
    """Return a human-readable escalation reason."""
    if incident.get("status") == "Closed":
        return ""
    severity = incident.get("severity")
    if severity == "Critical":
        return "Critical severity \u2013 automatic escalation"
    if severity == "High" and is_overdue(incident):
        return "High severity and overdue – automatic escalation"
    return ""


def apply_escalation(incident: dict) -> None:
    """Mutate an incident's escalation fields and log timeline events."""
    was_escalated = incident.get("escalated", False)
    incident["escalated"] = should_escalate(incident)
    incident["escalation_reason"] = get_escalation_reason(incident)

    # Log escalation state transitions
    if incident["escalated"] and not was_escalated:
        add_event(incident["id"], "escalated", incident["escalation_reason"])
    elif not incident["escalated"] and was_escalated:
        add_event(incident["id"], "de_escalated", "Escalation cleared")


def apply_escalation_all() -> None:
    for inc in incidents:
        apply_escalation(inc)
    check_overdue_warnings()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_incident(data: dict) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if not data.get(field, "").strip():
            errors.append(f"'{field}' is required.")

    severity = data.get("severity", "")
    if severity and severity not in VALID_SEVERITIES:
        errors.append(f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(VALID_SEVERITIES))}.")

    # Validate due_at is a valid ISO datetime
    due_at = data.get("due_at", "").strip()
    if due_at:
        try:
            datetime.fromisoformat(due_at)
        except ValueError:
            errors.append("'due_at' must be a valid ISO datetime string.")

    return errors


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def dashboard():
    apply_escalation_all()

    severity_filter = request.args.get("severity", "")
    status_filter = request.args.get("status", "")
    escalated_filter = request.args.get("escalated", "")

    filtered = incidents
    if severity_filter:
        filtered = [i for i in filtered if i["severity"] == severity_filter]
    if status_filter:
        filtered = [i for i in filtered if i["status"] == status_filter]
    if escalated_filter == "true":
        filtered = [i for i in filtered if i["escalated"]]
    elif escalated_filter == "false":
        filtered = [i for i in filtered if not i["escalated"]]

    total = len(incidents)
    open_count = sum(1 for i in incidents if i["status"] == "Open")
    critical_count = sum(1 for i in incidents if i["severity"] == "Critical")
    escalated_count = sum(1 for i in incidents if i["escalated"])

    timeline_map = get_timeline_map()
    unread_count = sum(1 for n in notification_log if not n["read"])

    return render_template(
        "index.html",
        incidents=filtered,
        total=total,
        open_count=open_count,
        critical_count=critical_count,
        escalated_count=escalated_count,
        severities=sorted(VALID_SEVERITIES),
        statuses=sorted(VALID_STATUSES),
        current_severity=severity_filter,
        current_status=status_filter,
        current_escalated=escalated_filter,
        is_overdue=is_overdue,
        timeline_map=timeline_map,
        roster=on_call_roster,
        unread_count=unread_count,
    )


@app.route("/incidents", methods=["GET"])
def list_incidents():
    apply_escalation_all()
    return jsonify(incidents)


@app.route("/incidents", methods=["POST"])
def create_incident():
    data = request.form.to_dict() if request.form else request.get_json(silent=True) or {}

    errors = validate_incident(data)
    if errors:
        if request.is_json:
            return jsonify({"errors": errors}), 400
        apply_escalation_all()
        return render_template(
            "index.html",
            incidents=incidents,
            total=len(incidents),
            open_count=sum(1 for i in incidents if i["status"] == "Open"),
            critical_count=sum(1 for i in incidents if i["severity"] == "Critical"),
            escalated_count=sum(1 for i in incidents if i["escalated"]),
            severities=sorted(VALID_SEVERITIES),
            statuses=sorted(VALID_STATUSES),
            current_severity="",
            current_status="",
            current_escalated="",
            is_overdue=is_overdue,
            form_errors=errors,
        ), 400

    incident = {
        "id": str(uuid.uuid4()),
        "title": data["title"].strip(),
        "description": data["description"].strip(),
        "severity": data["severity"].strip(),
        "impacted_service": data["impacted_service"].strip(),
        "owner": data["owner"].strip(),
        "created_at": datetime.utcnow().isoformat(),
        "due_at": data["due_at"].strip(),
        "status": "Open",
        "escalated": False,
        "escalation_reason": "",
        "assigned_to": "",
        "assignment_method": "manual",
        "notification_sent": False,
        "watchers": [],
        "overdue_notified": False,
    }
    add_event(incident["id"], "created", f"Incident created \u2013 {incident['severity']} severity")
    auto_assign(incident)
    apply_escalation(incident)
    if incident["escalated"]:
        notify_stakeholders(incident, "escalation", incident["escalation_reason"])
    incidents.append(incident)

    if request.is_json:
        return jsonify(incident), 201

    # Redirect-after-POST to prevent duplicate submissions
    from flask import redirect, url_for
    return redirect(url_for("dashboard"))


@app.route("/incidents/<incident_id>/status", methods=["POST"])
def update_status(incident_id):
    data = request.form.to_dict() if request.form else request.get_json(silent=True) or {}
    new_status = data.get("status", "").strip()

    if new_status not in VALID_STATUSES:
        msg = f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}."
        if request.is_json:
            return jsonify({"error": msg}), 400
        return msg, 400

    for inc in incidents:
        if inc["id"] == incident_id:
            old_status = inc["status"]
            inc["status"] = new_status
            add_event(inc["id"], "status_change", f"Status changed from {old_status} to {new_status}")
            apply_escalation(inc)
            notify_stakeholders(inc, "status_change",
                                f"Status changed from {old_status} to {new_status}")
            if request.is_json:
                return jsonify(inc)
            from flask import redirect, url_for
            return redirect(url_for("dashboard"))

    if request.is_json:
        return jsonify({"error": "Incident not found"}), 404
    return "Incident not found", 404


@app.route("/timeline/<incident_id>", methods=["GET"])
def incident_timeline(incident_id):
    events = get_timeline(incident_id)
    return jsonify(events)


# ---------------------------------------------------------------------------
# Roster CRUD routes
# ---------------------------------------------------------------------------


@app.route("/roster", methods=["GET"])
def list_roster():
    return jsonify(on_call_roster)


@app.route("/roster", methods=["POST"])
def add_roster_member():
    data = request.get_json(silent=True) or {}
    required = ["name", "email", "team"]
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"errors": [f"'{f}' is required." for f in missing]}), 400
    member = {
        "id": str(uuid.uuid4()),
        "name": data["name"].strip(),
        "email": data["email"].strip(),
        "team": data["team"].strip(),
        "on_call": bool(data.get("on_call", False)),
    }
    on_call_roster.append(member)
    return jsonify(member), 201


@app.route("/roster/<member_id>", methods=["PUT"])
def update_roster_member(member_id):
    data = request.get_json(silent=True) or {}
    for member in on_call_roster:
        if member["id"] == member_id:
            if "name" in data:
                member["name"] = data["name"].strip()
            if "email" in data:
                member["email"] = data["email"].strip()
            if "team" in data:
                member["team"] = data["team"].strip()
            if "on_call" in data:
                member["on_call"] = bool(data["on_call"])
            return jsonify(member)
    return jsonify({"error": "Roster member not found"}), 404


@app.route("/roster/<member_id>", methods=["DELETE"])
def delete_roster_member(member_id):
    for i, member in enumerate(on_call_roster):
        if member["id"] == member_id:
            on_call_roster.pop(i)
            return jsonify({"deleted": True})
    return jsonify({"error": "Roster member not found"}), 404


# ---------------------------------------------------------------------------
# Manual assignment route
# ---------------------------------------------------------------------------


@app.route("/incidents/<incident_id>/assign", methods=["POST"])
def assign_incident(incident_id):
    data = request.get_json(silent=True) or {}
    assigned_to = data.get("assigned_to", "").strip()
    if not assigned_to:
        return jsonify({"error": "'assigned_to' is required."}), 400

    for inc in incidents:
        if inc["id"] == incident_id:
            old_assignee = inc.get("assigned_to", "")
            inc["assigned_to"] = assigned_to
            inc["assignment_method"] = "manual"
            inc["notification_sent"] = True
            if old_assignee:
                add_event(inc["id"], "reassigned",
                          f"Reassigned from {old_assignee} to {assigned_to} (manual)")
            else:
                add_event(inc["id"], "assigned",
                          f"Assigned to {assigned_to} (manual)")
            send_notification(
                inc["id"], assigned_to, "in_app", "assignment",
                f"You have been assigned incident: {inc['title']}")
            return jsonify(inc)
    return jsonify({"error": "Incident not found"}), 404


# ---------------------------------------------------------------------------
# Notification routes
# ---------------------------------------------------------------------------


@app.route("/notifications", methods=["GET"])
def list_notifications():
    unread = request.args.get("unread", "")
    result = notification_log
    if unread == "true":
        result = [n for n in result if not n["read"]]
    return jsonify(list(reversed(result)))


@app.route("/incidents/<incident_id>/notifications", methods=["GET"])
def incident_notifications(incident_id):
    result = [n for n in notification_log if n["incident_id"] == incident_id]
    return jsonify(list(reversed(result)))


@app.route("/notifications/<notif_id>/read", methods=["POST"])
def mark_notification_read(notif_id):
    for n in notification_log:
        if n["id"] == notif_id:
            n["read"] = True
            return jsonify(n)
    return jsonify({"error": "Notification not found"}), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    apply_escalation_all()
    debug_mode = os.environ.get("FLASK_ENV") != "production"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug_mode, port=port)
