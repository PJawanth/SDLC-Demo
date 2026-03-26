from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import uuid
import copy

app = Flask(__name__)

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
    if incident["status"] == "Closed":
        return False
    if incident["severity"] == "Critical":
        return True
    if incident["severity"] == "High" and is_overdue(incident):
        return True
    return False


def get_escalation_reason(incident: dict) -> str:
    """Return a human-readable escalation reason."""
    if incident["status"] == "Closed":
        return ""
    if incident["severity"] == "Critical":
        return "Critical severity – automatic escalation"
    if incident["severity"] == "High" and is_overdue(incident):
        return "High severity and overdue – automatic escalation"
    return ""


def apply_escalation(incident: dict) -> None:
    """Mutate an incident's escalation fields based on current rules."""
    incident["escalated"] = should_escalate(incident)
    incident["escalation_reason"] = get_escalation_reason(incident)


def apply_escalation_all() -> None:
    for inc in incidents:
        apply_escalation(inc)


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
    }
    apply_escalation(incident)
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
            inc["status"] = new_status
            apply_escalation(inc)
            if request.is_json:
                return jsonify(inc)
            from flask import redirect, url_for
            return redirect(url_for("dashboard"))

    if request.is_json:
        return jsonify({"error": "Incident not found"}), 404
    return "Incident not found", 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    apply_escalation_all()
    app.run(debug=True, port=5000)
