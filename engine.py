
import os
import hmac
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_sqlalchemy import SQLAlchemy

engine = Blueprint("engine", __name__)
db = SQLAlchemy()

class Fulfillment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, nullable=False, unique=True)
    mode = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(30), default="Queued")
    provider = db.Column(db.String(100))
    campaign_notes = db.Column(db.Text)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

class FulfillmentLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

def admin_authorized():
    supplied = request.headers.get("X-Admin-Key", "")
    expected = os.environ.get("ADMIN_API_KEY", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)

def record(order_id, action):
    db.session.add(FulfillmentLog(
        order_id=order_id,
        action=action
    ))

@engine.route("/api/engine/route/<int:order_id>", methods=["POST"])
def route_order(order_id):
    if not admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    mode = data.get("mode")

    if mode not in ("provider", "campaign"):
        return jsonify({"error": "Invalid fulfillment mode"}), 400

    task = Fulfillment.query.filter_by(order_id=order_id).first()

    if task:
        return jsonify({"error": "Order already routed"}), 409

    task = Fulfillment(
        order_id=order_id,
        mode=mode,
        status="Queued"
    )

    db.session.add(task)
    record(order_id, "Routed to " + mode)
    db.session.commit()

    return jsonify({
        "order_id": order_id,
        "mode": mode,
        "status": "Queued"
    }), 201

@engine.route("/api/engine/queue")
def queue():
    if not admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    tasks = Fulfillment.query.order_by(
        Fulfillment.created_at.desc()
    ).all()

    return jsonify([{
        "id": t.id,
        "order_id": t.order_id,
        "mode": t.mode,
        "status": t.status,
        "provider": t.provider,
        "notes": t.campaign_notes
    } for t in tasks])

@engine.route("/api/engine/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    if not admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    task = db.session.get(Fulfillment, task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    data = request.get_json(silent=True) or {}
    status = data.get("status")

    allowed = {
        "Queued",
        "Processing",
        "Completed",
        "Failed",
        "Cancelled"
    }

    if status not in allowed:
        return jsonify({"error": "Invalid status"}), 400

    task.status = status
    task.provider = data.get("provider", task.provider)
    task.campaign_notes = data.get("notes", task.campaign_notes)

    record(task.order_id, "Status changed to " + status)
    db.session.commit()

    return jsonify({
        "task_id": task.id,
        "status": task.status
    })

@engine.route("/api/engine/logs/<int:order_id>")
def logs(order_id):
    if not admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    entries = FulfillmentLog.query.filter_by(
        order_id=order_id
    ).order_by(FulfillmentLog.created_at.asc()).all()

    return jsonify([
        {
            "action": entry.action,
            "created_at": entry.created_at.isoformat()
        }
        for entry in entries
    ])
