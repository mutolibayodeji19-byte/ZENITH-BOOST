
import os
import hmac
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash


# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__, static_folder=".", static_url_path="")

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///zenith_boost.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY"),
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True
)


# ZENITH BOOST FRONTEND CORS CONFIGURATION
ALLOWED_FRONTENDS = {
    "https://zenith-boost.onrender.com"
}

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")

    if origin in ALLOWED_FRONTENDS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"

    return response

if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY environment variable is missing.")

db = SQLAlchemy(app)


# =========================================================
# SERVICE CATALOG
# =========================================================

SERVICES = {
    "instagram": ["Likes", "Followers", "Comments"],
    "tiktok": ["Likes", "Followers", "Comments"],
    "facebook": ["Likes", "Followers", "Comments"],
    "youtube": ["Likes", "Subscribers", "Comments"],
    "telegram": ["Members", "Post Views"],
    "x": ["Followers", "Likes", "Comments"]
}

ALLOWED_DOMAINS = {
    "instagram": ["instagram.com", "www.instagram.com"],
    "tiktok": ["tiktok.com", "www.tiktok.com", "vm.tiktok.com"],
    "facebook": ["facebook.com", "www.facebook.com", "fb.watch"],
    "youtube": ["youtube.com", "www.youtube.com", "youtu.be"],
    "telegram": ["t.me", "telegram.me"],
    "x": ["x.com", "www.x.com", "twitter.com", "www.twitter.com"]
}


# =========================================================
# DATABASE MODELS
# =========================================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )
    platform = db.Column(db.String(30), nullable=False)
    service = db.Column(db.String(30), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="Pending")
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# Separate table: does not alter existing Order columns.
class PaymentVerification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("order.id"),
        unique=True,
        nullable=False
    )
    status = db.Column(db.String(30), default="Unverified")
    payment_reference = db.Column(db.String(150))
    admin_note = db.Column(db.Text)
    verified_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class Fulfillment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("order.id"),
        nullable=False,
        unique=True
    )
    mode = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), default="Queued")
    provider = db.Column(db.String(100))
    campaign_notes = db.Column(db.Text)
    evidence_url = db.Column(db.String(500))
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
    details = db.Column(db.Text)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


with app.app_context():
    db.create_all()


# =========================================================
# COMMON HELPERS
# =========================================================

def response(message, status=200, **extra):
    return jsonify({
        "message": message,
        **extra
    }), status


def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def require_login():
    user = current_user()

    if not user:
        return None, response("Please log in first.", 401)

    return user, None


def require_admin():
    configured_key = os.environ.get("ADMIN_API_KEY")
    supplied_key = request.headers.get("X-Admin-Key", "")

    if not configured_key:
        return response("Admin API is not configured.", 503)

    if not hmac.compare_digest(
        supplied_key.encode("utf-8"),
        configured_key.encode("utf-8")
    ):
        return response("Unauthorized admin request.", 401)

    return None


def log_action(order_id, action, details=None):
    db.session.add(FulfillmentLog(
        order_id=order_id,
        action=action,
        details=details
    ))


def payment_is_verified(order_id):
    payment = PaymentVerification.query.filter_by(
        order_id=order_id
    ).first()

    return payment and payment.status == "Verified"


def fulfillment_json(task):
    return {
        "id": task.id,
        "order_id": task.order_id,
        "mode": task.mode,
        "status": task.status,
        "provider": task.provider,
        "campaign_notes": task.campaign_notes,
        "evidence_url": task.evidence_url,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat()
    }


# =========================================================
# WEBSITE AND HEALTH
# =========================================================

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))

        return jsonify({
            "status": "online",
            "project": "ZENITH BOOST",
            "database": "connected"
        })

    except Exception:
        app.logger.exception("Database health check failed")

        return jsonify({
            "status": "error",
            "project": "ZENITH BOOST",
            "database": "disconnected"
        }), 503


@app.route("/api/services")
def services():
    return jsonify(SERVICES)


# =========================================================
# AUTHENTICATION
# =========================================================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not 3 <= len(username) <= 40:
        return response("Username must be 3–40 characters.", 400)

    if len(email) > 120 or "@" not in email:
        return response("Enter a valid email.", 400)

    if len(password) < 10:
        return response("Password must contain at least 10 characters.", 400)

    if User.query.filter_by(username=username).first():
        return response("Username already exists.", 409)

    if User.query.filter_by(email=email).first():
        return response("Email already registered.", 409)

    user = User(
        username=username,
        email=email,
        password=generate_password_hash(password)
    )

    db.session.add(user)
    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    return response(
        "Account created successfully.",
        201,
        username=user.username
    )


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password, password):
        return response("Invalid email or password.", 401)

    session.clear()
    session["user_id"] = user.id

    return response("Login successful.", username=user.username)


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return response("Logged out successfully.")


@app.route("/api/me")
def me():
    user, error = require_login()

    if error:
        return error

    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at.isoformat()
    })


# =========================================================
# CUSTOMER ORDERS
# =========================================================

@app.route("/api/orders", methods=["POST"])
def create_order():
    user, error = require_login()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    platform = str(data.get("platform", "")).lower()
    service = str(data.get("service", "")).strip()
    link = str(data.get("link", "")).strip()

    try:
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return response("Invalid quantity.", 400)

    if platform not in SERVICES:
        return response("Invalid platform.", 400)

    if service not in SERVICES[platform]:
        return response("Invalid service.", 400)

    if not 1 <= quantity <= 100000:
        return response("Quantity must be between 1 and 100000.", 400)

    if len(link) > 500:
        return response("Link is too long.", 400)

    parsed = urlparse(link)

    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_DOMAINS[platform]
        or parsed.username
        or parsed.password
    ):
        return response("Enter a valid public platform URL.", 400)

    order = Order(
        user_id=user.id,
        platform=platform,
        service=service,
        link=link,
        quantity=quantity,
        status="Pending"
    )

    db.session.add(order)
    db.session.commit()

    return response(
        "Order received and awaiting payment review.",
        201,
        order_id=order.id,
        status=order.status
    )


@app.route("/api/orders", methods=["GET"])
def my_orders():
    user, error = require_login()

    if error:
        return error

    orders = Order.query.filter_by(
        user_id=user.id
    ).order_by(Order.created_at.desc()).all()

    results = []

    for order in orders:
        payment = PaymentVerification.query.filter_by(
            order_id=order.id
        ).first()

        task = Fulfillment.query.filter_by(
            order_id=order.id
        ).first()

        results.append({
            "id": order.id,
            "platform": order.platform,
            "service": order.service,
            "link": order.link,
            "quantity": order.quantity,
            "status": order.status,
            "payment_status": payment.status if payment else "Unverified",
            "fulfillment": fulfillment_json(task) if task else None,
            "created_at": order.created_at.isoformat()
        })

    return jsonify(results)


# =========================================================
# ADMIN: PAYMENT CONFIRMATION
# =========================================================

@app.route(
    "/api/engine/verify-payment/<int:order_id>",
    methods=["POST"]
)
def verify_payment(order_id):
    error = require_admin()

    if error:
        return error

    order = db.session.get(Order, order_id)

    if not order:
        return response("Order not found.", 404)

    data = request.get_json(silent=True) or {}

    reference = str(
        data.get("payment_reference", "")
    ).strip()

    note = str(data.get("admin_note", "")).strip()

    if not reference:
        return response("Payment reference is required.", 400)

    if len(reference) > 150:
        return response("Payment reference is too long.", 400)

    payment = PaymentVerification.query.filter_by(
        order_id=order_id
    ).first()

    if not payment:
        payment = PaymentVerification(order_id=order_id)
        db.session.add(payment)

    payment.status = "Verified"
    payment.payment_reference = reference
    payment.admin_note = note
    payment.verified_at = datetime.now(timezone.utc)

    order.status = "Paid - Awaiting Fulfillment"

    log_action(
        order_id,
        "Payment manually confirmed",
        "Reference: " + reference
    )

    db.session.commit()

    return response(
        "Payment marked as verified. Ensure the transaction was checked externally.",
        order_id=order_id,
        payment_status=payment.status
    )


# =========================================================
# ADMIN: FULFILLMENT ROUTER
# =========================================================

@app.route(
    "/api/engine/route/<int:order_id>",
    methods=["POST"]
)
def route_fulfillment(order_id):
    error = require_admin()

    if error:
        return error

    order = db.session.get(Order, order_id)

    if not order:
        return response("Order not found.", 404)

    if not payment_is_verified(order_id):
        return response(
            "Payment must be verified before fulfillment.",
            403
        )

    if Fulfillment.query.filter_by(order_id=order_id).first():
        return response(
            "This order already has a fulfillment task.",
            409
        )

    data = request.get_json(silent=True) or {}

    mode = str(data.get("mode", "")).lower()
    notes = str(data.get("campaign_notes", "")).strip()

    if mode not in ["campaign", "provider"]:
        return response(
            "Mode must be campaign or provider.",
            400
        )

    # Provider execution is intentionally blocked until
    # a real provider API has been configured and tested.
    if mode == "provider":
        return response(
            "Provider integration is not configured yet. "
            "No provider order has been submitted.",
            501
        )

    task = Fulfillment(
        order_id=order_id,
        mode="campaign",
        status="Queued",
        campaign_notes=notes
    )

    order.status = "Queued for Campaign"

    db.session.add(task)

    log_action(
        order_id,
        "Campaign fulfillment queued",
        notes or "No campaign notes supplied."
    )

    db.session.commit()

    return response(
        "Campaign task queued.",
        201,
        task=fulfillment_json(task)
    )


# =========================================================
# ADMIN: FULFILLMENT QUEUE
# =========================================================

@app.route("/api/engine/queue", methods=["GET"])
def fulfillment_queue():
    error = require_admin()

    if error:
        return error

    tasks = Fulfillment.query.order_by(
        Fulfillment.created_at.desc()
    ).all()

    results = []

    for task in tasks:
        order = db.session.get(Order, task.order_id)

        results.append({
            **fulfillment_json(task),
            "order": {
                "platform": order.platform,
                "service": order.service,
                "link": order.link,
                "quantity": order.quantity
            } if order else None
        })

    return jsonify(results)


# =========================================================
# ADMIN: UPDATE FULFILLMENT
# =========================================================

@app.route(
    "/api/engine/update/<int:task_id>",
    methods=["POST"]
)
def update_fulfillment(task_id):
    error = require_admin()

    if error:
        return error

    task = db.session.get(Fulfillment, task_id)

    if not task:
        return response("Fulfillment task not found.", 404)

    data = request.get_json(silent=True) or {}

    new_status = str(data.get("status", "")).strip().title()
    notes = str(data.get("campaign_notes", "")).strip()
    evidence_url = str(data.get("evidence_url", "")).strip()

    allowed_statuses = [
        "Queued",
        "Processing",
        "Completed",
        "Partial",
        "Failed",
        "Cancelled"
    ]

    if new_status not in allowed_statuses:
        return response("Invalid fulfillment status.", 400)

    if new_status == "Completed" and not notes:
        return response(
            "Completion notes are required before marking completed.",
            400
        )

    if evidence_url:
        parsed = urlparse(evidence_url)

        if parsed.scheme != "https" or not parsed.hostname:
            return response("Evidence URL must be a valid HTTPS URL.", 400)

    task.status = new_status

    if notes:
        task.campaign_notes = notes

    if evidence_url:
        task.evidence_url = evidence_url

    order = db.session.get(Order, task.order_id)

    if order:
        order.status = new_status

    log_action(
        task.order_id,
        "Fulfillment status changed",
        "Status: " + new_status + ". " + notes
    )

    db.session.commit()

    return response(
        "Fulfillment task updated.",
        task=fulfillment_json(task)
    )


# =========================================================
# ADMIN: ORDER AUDIT LOGS
# =========================================================

@app.route(
    "/api/engine/logs/<int:order_id>",
    methods=["GET"]
)
def fulfillment_logs(order_id):
    error = require_admin()

    if error:
        return error

    order = db.session.get(Order, order_id)

    if not order:
        return response("Order not found.", 404)

    logs = FulfillmentLog.query.filter_by(
        order_id=order_id
    ).order_by(FulfillmentLog.created_at.desc()).all()

    return jsonify([
        {
            "id": log.id,
            "order_id": log.order_id,
            "action": log.action,
            "details": log.details,
            "created_at": log.created_at.isoformat()
        }
        for log in logs
    ])


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
