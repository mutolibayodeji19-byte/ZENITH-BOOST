
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder=".", static_url_path="")

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "development-only-change-this")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///zenith_boost.db"
).replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

db = SQLAlchemy(app)

SERVICES = {
    "instagram": ["Likes", "Followers", "Comments"],
    "tiktok": ["Likes", "Followers", "Comments"],
    "facebook": ["Likes", "Followers", "Comments"],
    "youtube": ["Likes", "Subscribers", "Comments"],
    "telegram": ["Members", "Post Views"],
    "x": ["Followers", "Likes", "Comments"]
}

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    platform = db.Column(db.String(30), nullable=False)
    service = db.Column(db.String(30), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="Pending")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

with app.app_context():
    db.create_all()

def response(message, status=200, **extra):
    return jsonify({"message": message, **extra}), status

def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None

@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "online", "project": "ZENITH BOOST"})

@app.route("/api/services")
def services():
    return jsonify(SERVICES)

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if len(username) < 3 or len(username) > 40:
        return response("Username must be 3–40 characters.", 400)
    if "@" not in email or len(email) > 120:
        return response("Enter a valid email.", 400)
    if len(password) < 10:
        return response("Password must be at least 10 characters.", 400)
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
    session["user_id"] = user.id
    return response("Account created.", 201, username=user.username)

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password, password):
        return response("Invalid email or password.", 401)

    session["user_id"] = user.id
    return response("Login successful.", username=user.username)

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return response("Logged out.")

@app.route("/api/me")
def me():
    user = current_user()
    if not user:
        return response("Please log in.", 401)
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email
    })

@app.route("/api/orders", methods=["POST"])
def create_order():
    user = current_user()
    if not user:
        return response("Please log in first.", 401)

    data = request.get_json(silent=True) or {}
    platform = str(data.get("platform", "")).lower()
    service = str(data.get("service", "")).strip()
    link = str(data.get("link", "")).strip()
    quantity = data.get("quantity")

    if platform not in SERVICES or service not in SERVICES[platform]:
        return response("Invalid service selection.", 400)

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return response("Invalid quantity.", 400)

    if not 1 <= quantity <= 100000:
        return response("Quantity must be between 1 and 100,000.", 400)

    parsed = urlparse(link)
    if parsed.scheme != "https" or not parsed.netloc:
        return response("Enter a valid HTTPS link.", 400)

    order = Order(
        user_id=user.id,
        platform=platform,
        service=service,
        link=link,
        quantity=quantity
    )
    db.session.add(order)
    db.session.commit()

    return response(
        "Order received. Awaiting review and fulfillment.",
        201,
        order_id=order.id,
        status=order.status
    )

@app.route("/api/orders")
def my_orders():
    user = current_user()
    if not user:
        return response("Please log in first.", 401)

    orders = Order.query.filter_by(user_id=user.id).order_by(
        Order.created_at.desc()
    ).all()

    return jsonify([{
        "id": o.id,
        "platform": o.platform,
        "service": o.service,
        "quantity": o.quantity,
        "status": o.status,
        "created_at": o.created_at.isoformat()
    } for o in orders])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
