
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder=".", static_url_path="")

database_url = os.environ.get("DATABASE_URL", "sqlite:///zenith_boost.db")
database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY"),
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True
)

if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY environment variable is missing.")

db = SQLAlchemy(app)

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
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    platform = db.Column(db.String(30), nullable=False)
    service = db.Column(db.String(30), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="Pending")
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


with app.app_context():
    db.create_all()


def response(message, status=200, **extra):
    return jsonify({"message": message, **extra}), status


def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def require_login():
    user = current_user()
    if not user:
        return None, response("Please log in first.", 401)
    return user, None


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

    return response("Account created successfully.", 201,
                    username=user.username)


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
        "Order received and awaiting review.",
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

    return jsonify([
        {
            "id": order.id,
            "platform": order.platform,
            "service": order.service,
            "quantity": order.quantity,
            "status": order.status,
            "created_at": order.created_at.isoformat()
        }
        for order in orders
    ])


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
