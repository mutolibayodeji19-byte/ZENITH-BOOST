import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import jsonify, request, session
from sqlalchemy import update


PAYSTACK_BASE_URL = "https://api.paystack.co"


def register_paystack_wallet(app, db, User, current_user):
    """
    Register Paystack wallet routes and create the two wallet tables.

    Call this once, after the main app's User model and current_user()
    helper have been defined.
    """

    class Wallet(db.Model):
        __tablename__ = "wallet"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"),
            unique=True, nullable=False
        )
        balance_kobo = db.Column(db.BigInteger, default=0, nullable=False)
        created_at = db.Column(
            db.DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            nullable=False
        )
        updated_at = db.Column(
            db.DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            onupdate=lambda: datetime.now(timezone.utc),
            nullable=False
        )

    class WalletTransaction(db.Model):
        __tablename__ = "wallet_transaction"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"), nullable=False
        )
        reference = db.Column(db.String(100), unique=True, nullable=False)
        amount_kobo = db.Column(db.BigInteger, nullable=False)
        currency = db.Column(db.String(3), default="NGN", nullable=False)
        gateway = db.Column(db.String(30), default="Paystack", nullable=False)
        status = db.Column(db.String(20), default="pending", nullable=False)
        credited = db.Column(db.Boolean, default=False, nullable=False)
        created_at = db.Column(
            db.DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            nullable=False
        )
        paid_at = db.Column(db.DateTime(timezone=True))

    with app.app_context():
        db.create_all()

    def api_response(message, status=200, **extra):
        return jsonify({"message": message, **extra}), status

    def paystack_secret():
        return os.environ.get("PAYSTACK_SECRET_KEY", "").strip()

    def paystack_request(path, method="GET", body=None):
        secret = paystack_secret()
        if not secret:
            return None, "PAYSTACK_SECRET_KEY is not configured."

        headers = {
            "Authorization": "Bearer " + secret,
            "Accept": "application/json"
        }
        raw_body = None

        if body is not None:
            headers["Content-Type"] = "application/json"
            raw_body = json.dumps(body).encode("utf-8")

        req = Request(
            PAYSTACK_BASE_URL + path,
            data=raw_body,
            headers=headers,
            method=method
        )

        try:
            with urlopen(req, timeout=25) as result:
                payload = json.loads(result.read().decode("utf-8"))
                return payload, None
        except HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
                message = details.get("message", "Paystack request failed.")
            except Exception:
                message = "Paystack returned HTTP " + str(exc.code) + "."
            app.logger.warning("Paystack HTTP error: %s", exc.code)
            return None, message
        except (URLError, TimeoutError):
            app.logger.warning("Could not connect to Paystack.")
            return None, "Could not connect to Paystack."
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "Paystack returned invalid JSON."
        except Exception:
            app.logger.exception("Paystack request failed.")
            return None, "Paystack request failed."

    def get_or_create_wallet(user_id):
        wallet = Wallet.query.filter_by(user_id=user_id).first()
        if wallet:
            return wallet

        wallet = Wallet(user_id=user_id, balance_kobo=0)
        db.session.add(wallet)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            wallet = Wallet.query.filter_by(user_id=user_id).first()
            if wallet is None:
                raise
        return wallet

    def verify_with_paystack(reference):
        payload, error = paystack_request(
            "/transaction/verify/" + reference
        )
        if error:
            return None, error

        if not payload or payload.get("status") is not True:
            return None, "Paystack could not verify this transaction."

        data = payload.get("data") or {}
        if data.get("reference") != reference:
            return None, "Paystack reference does not match."

        if data.get("status") != "success":
            return None, "Payment has not succeeded."

        return data, None

    def credit_verified_transaction(reference, verified_data):
        transaction = WalletTransaction.query.filter_by(
            reference=reference
        ).first()

        if transaction is None:
            return False, "Transaction reference was not found."

        expected_amount = int(transaction.amount_kobo)
        try:
            paid_amount = int(verified_data.get("amount"))
        except (TypeError, ValueError):
            return False, "Paystack returned an invalid amount."

        if paid_amount != expected_amount:
            return False, "Payment amount does not match the deposit."
        if verified_data.get("currency") != transaction.currency:
            return False, "Payment currency does not match."
        if verified_data.get("reference") != transaction.reference:
            return False, "Payment reference does not match."

        user = db.session.get(User, transaction.user_id)
        if user is None:
            return False, "Wallet owner was not found."

        customer = verified_data.get("customer") or {}
        paid_email = str(customer.get("email", "")).strip().lower()
        if paid_email and paid_email != user.email.strip().lower():
            return False, "Payment email does not match the account."

        wallet = Wallet.query.filter_by(
            user_id=transaction.user_id
        ).first()
        if wallet is None:
            wallet = Wallet(user_id=transaction.user_id, balance_kobo=0)
            db.session.add(wallet)
            db.session.flush()

        # Conditional update makes crediting idempotent: only the first
        # verified request can change credited from False to True.
        result = db.session.execute(
            update(WalletTransaction)
            .where(
                WalletTransaction.id == transaction.id,
                WalletTransaction.credited.is_(False)
            )
            .values(
                credited=True,
                status="success",
                paid_at=datetime.now(timezone.utc)
            )
        )

        if result.rowcount == 1:
            wallet.balance_kobo = (
                int(wallet.balance_kobo) + expected_amount
            )
            db.session.commit()
            return True, "Wallet credited successfully."

        db.session.rollback()
        return True, "This payment was already credited."

    @app.route("/api/wallet", methods=["GET"])
    def wallet_details():
        user = current_user()
        if not user:
            return api_response("Please log in first.", 401)

        wallet = get_or_create_wallet(user.id)
        transactions = WalletTransaction.query.filter_by(
            user_id=user.id
        ).order_by(WalletTransaction.created_at.desc()).limit(50).all()

        return jsonify({
            "balance_kobo": int(wallet.balance_kobo),
            "balance_naira": int(wallet.balance_kobo) / 100,
            "currency": "NGN",
            "transactions": [
                {
                    "reference": item.reference,
                    "amount_kobo": int(item.amount_kobo),
                    "amount_naira": int(item.amount_kobo) / 100,
                    "currency": item.currency,
                    "status": item.status,
                    "credited": bool(item.credited),
                    "gateway": item.gateway,
                    "created_at": item.created_at.isoformat(),
                    "paid_at": (
                        item.paid_at.isoformat() if item.paid_at else None
                    )
                }
                for item in transactions
            ]
        })

    @app.route("/api/wallet/deposit", methods=["POST"])
    def initialize_wallet_deposit():
        user = current_user()
        if not user:
            return api_response("Please log in first.", 401)

        if not paystack_secret():
            return api_response(
                "Paystack is not configured on the server.", 503
            )

        data = request.get_json(silent=True) or {}
        raw_amount = data.get("amount")

        # Accept whole naira only. Never use a floating-point amount.
        if isinstance(raw_amount, bool):
            return api_response("Enter a valid deposit amount.", 400)

        try:
            amount_naira = int(str(raw_amount).strip())
        except (TypeError, ValueError):
            return api_response("Enter a valid deposit amount.", 400)

        if str(raw_amount).strip() != str(amount_naira):
            return api_response(
                "Deposit amount must be a whole number of naira.", 400
            )

        if not 100 <= amount_naira <= 10000:
            return api_response(
                "Deposit must be between ₦100 and ₦10,000.", 400
            )

        get_or_create_wallet(user.id)

        reference = "ZB-" + secrets.token_hex(16).upper()
        amount_kobo = amount_naira * 100

        transaction = WalletTransaction(
            user_id=user.id,
            reference=reference,
            amount_kobo=amount_kobo,
            currency="NGN",
            status="pending",
            credited=False
        )
        db.session.add(transaction)
        db.session.commit()

        callback_url = os.environ.get(
            "PAYSTACK_CALLBACK_URL",
            "https://zenith-boost.onrender.com/"
        ).strip()

        payload, error = paystack_request(
            "/transaction/initialize",
            method="POST",
            body={
                "email": user.email,
                "amount": amount_kobo,
                "currency": "NGN",
                "reference": reference,
                "callback_url": callback_url,
                "metadata": {
                    "user_id": user.id,
                    "purpose": "ZENITH BOOST wallet deposit"
                }
            }
        )

        if error:
            transaction.status = "failed"
            db.session.commit()
            return api_response(error, 502)

        result = (payload or {}).get("data") or {}
        authorization_url = result.get("authorization_url")

        if (payload or {}).get("status") is not True or not authorization_url:
            transaction.status = "failed"
            db.session.commit()
            return api_response(
                "Paystack did not return a checkout link.", 502
            )

        return api_response(
            "Deposit initialized. Complete payment on Paystack.",
            200,
            authorization_url=authorization_url,
            reference=reference,
            amount_naira=amount_naira,
            amount_kobo=amount_kobo,
            currency="NGN"
        )

    @app.route(
        "/api/wallet/verify/<string:reference>",
        methods=["GET", "POST"]
    )
    def verify_wallet_deposit(reference):
        user = current_user()
        if not user:
            return api_response("Please log in first.", 401)

        if len(reference) > 100:
            return api_response("Invalid payment reference.", 400)

        transaction = WalletTransaction.query.filter_by(
            reference=reference,
            user_id=user.id
        ).first()

        if transaction is None:
            return api_response("Deposit transaction not found.", 404)

        if transaction.credited:
            wallet = Wallet.query.filter_by(user_id=user.id).first()
            return api_response(
                "This deposit has already been credited.",
                balance_naira=(
                    int(wallet.balance_kobo) / 100 if wallet else 0
                ),
                reference=reference
            )

        verified_data, error = verify_with_paystack(reference)
        if error:
            return api_response(error, 400)

        credited, message = credit_verified_transaction(
            reference, verified_data
        )
        if not credited:
            return api_response(message, 400)

        wallet = Wallet.query.filter_by(user_id=user.id).first()
        return api_response(
            message,
            balance_naira=(
                int(wallet.balance_kobo) / 100 if wallet else 0
            ),
            reference=reference
        )

    @app.route("/api/paystack/webhook", methods=["POST"])
    def paystack_webhook():
        secret = paystack_secret()
        if not secret:
            return api_response("Paystack is not configured.", 503)

        signature = request.headers.get("x-paystack-signature", "")
        raw_body = request.get_data(cache=True)

        expected_signature = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha512
        ).hexdigest()

        if not signature or not hmac.compare_digest(
            signature, expected_signature
        ):
            return api_response("Invalid webhook signature.", 401)

        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return api_response("Invalid webhook body.", 400)

        if event.get("event") != "charge.success":
            return jsonify({"received": True})

        event_data = event.get("data") or {}
        reference = str(event_data.get("reference", "")).strip()

        if not reference or len(reference) > 100:
            return api_response("Invalid payment reference.", 400)

        transaction = WalletTransaction.query.filter_by(
            reference=reference
        ).first()

        # Ignore valid Paystack events for transactions not created here.
        if transaction is None:
            return jsonify({"received": True})

        if transaction.credited:
            return jsonify({"received": True, "already_credited": True})

        verified_data, error = verify_with_paystack(reference)
        if error:
            app.logger.warning(
                "Paystack webhook verification failed: %s", error
            )
            return api_response("Payment verification failed.", 400)

        credited, message = credit_verified_transaction(
            reference, verified_data
        )
        if not credited:
            app.logger.warning(
                "Paystack webhook was not credited: %s", message
            )
            return api_response("Payment could not be credited.", 400)

        return jsonify({"received": True, "credited": True})
