from __future__ import annotations

import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from flask import Flask, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db
import mail
from errors import ApiError, error_response
from utils import client_ip

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
VERIFICATION_TOKEN_TTL = timedelta(hours=24)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5173").rstrip("/")

# Compared against on "user not found" so login takes roughly the same time
# whether or not the email exists -- avoids leaking account existence via
# response timing.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_hex(32))

_rate_limit_lock = threading.Lock()
_rate_limit_hits: dict[str, list[float]] = {}


def _check_rate_limit(key: str, *, max_hits: int, window_seconds: float) -> None:
    now = time.monotonic()
    with _rate_limit_lock:
        hits = [t for t in _rate_limit_hits.get(key, []) if now - t < window_seconds]
        if len(hits) >= max_hits:
            raise ApiError(
                429,
                "Too many requests",
                "Too many attempts. Please wait a few minutes and try again.",
            )
        hits.append(now)
        _rate_limit_hits[key] = hits


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _send_verification_email(email: str, token: str) -> None:
    verify_url = f"{PUBLIC_BASE_URL}/api/auth/verify-email?token={token}"
    mail.send_email(
        email,
        "Verify your email - Stock AI Orchestrator",
        (
            "Welcome!\n\n"
            "Please verify your email address by opening this link:\n"
            f"{verify_url}\n\n"
            "This link expires in 24 hours. If you didn't request this, "
            "you can ignore this email."
        ),
    )


def _verify_result_page(success: bool, message: str):
    title = "Email verified" if success else "Verification failed"
    color = "#4ade80" if success else "#f87171"
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #07111f; color: #e8eef7;
          display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .card {{ text-align: center; padding: 32px 40px; border-radius: 16px; background: #101e32;
           border: 1px solid rgba(148,163,184,.18); max-width: 420px; }}
  h1 {{ color: {color}; font-size: 20px; margin: 0 0 12px; }}
  p {{ color: #9fb0c9; font-size: 14px; line-height: 1.6; margin: 0; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
    return html, 200 if success else 400


def register_auth_routes(app: Flask) -> None:
    @app.post("/api/auth/register")
    def register():
        """
        Register a new account with email and password.
        ---
        tags:
          - Auth
        summary: Register with email/password
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [email, password]
                properties:
                  email:
                    type: string
                  password:
                    type: string
        responses:
          201:
            description: Registered; verification email sent
          400:
            description: Invalid email or password
          409:
            description: Email already registered
          429:
            description: Too many attempts
          500:
            description: Could not create account or send verification email
        """
        _check_rate_limit(f"register:{client_ip()}", max_hits=5, window_seconds=3600)

        payload = request.get_json(silent=True) or {}
        email = _normalize_email(payload.get("email", ""))
        password = payload.get("password") or ""

        if not EMAIL_PATTERN.match(email):
            return error_response(
                400, "Invalid email", "Please provide a valid email address."
            )
        if len(password) < MIN_PASSWORD_LENGTH:
            return error_response(
                400,
                "Invalid password",
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            )

        password_hash = generate_password_hash(password)
        token = _generate_token()
        expires_at = datetime.now(timezone.utc) + VERIFICATION_TOKEN_TTL

        try:
            user = db.create_user(email, password_hash, token, expires_at)
        except psycopg2.errors.UniqueViolation:
            return error_response(
                409,
                "Email already registered",
                "An account with this email already exists.",
            )

        try:
            _send_verification_email(email, token)
        except Exception as exc:
            return error_response(
                500,
                "Could not send verification email",
                f"Your account was created but the verification email "
                f"could not be sent: {exc}",
            )

        return (
            jsonify(
                {
                    "message": "Registration successful. Check your email to verify your account.",
                    "email": user["email"],
                }
            ),
            201,
        )

    @app.get("/api/auth/verify-email")
    def verify_email():
        """
        Verify an email address from the link sent at registration.
        ---
        tags:
          - Auth
        summary: Verify email via token
        parameters:
          - in: query
            name: token
            required: true
            schema:
              type: string
        responses:
          200:
            description: Verified (HTML confirmation page)
          400:
            description: Invalid or expired token (HTML page)
        """
        if request.method == "HEAD":
            # Flask routes HEAD to this same view by default. Email clients
            # (e.g. Outlook Safe Links) send a HEAD request to prescan links
            # before the user ever clicks -- HEAD must be side-effect-free
            # per HTTP semantics, so skip token consumption here entirely.
            return "", 200

        token = request.args.get("token", "")
        user = db.get_user_by_verification_token(token) if token else None

        if not user or not user["verification_token_expires_at"]:
            return _verify_result_page(
                False, "This verification link is invalid."
            )

        expires_at = user["verification_token_expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return _verify_result_page(
                False,
                "This verification link has expired. Request a new one from the login screen.",
            )

        db.mark_email_verified(user["id"])
        return _verify_result_page(
            True, "Your email is verified. You can close this tab and log in."
        )

    @app.post("/api/auth/resend-verification")
    def resend_verification():
        """
        Resend the verification email if the account exists and isn't verified.
        ---
        tags:
          - Auth
        summary: Resend verification email
        responses:
          200:
            description: >
              Always a generic success message, regardless of whether the
              account exists, to avoid leaking which emails are registered.
          429:
            description: Too many attempts
        """
        _check_rate_limit(f"resend:{client_ip()}", max_hits=5, window_seconds=3600)

        payload = request.get_json(silent=True) or {}
        email = _normalize_email(payload.get("email", ""))
        generic_message = {
            "message": "If that account exists and needs verification, we've sent a new email."
        }

        if not EMAIL_PATTERN.match(email):
            return jsonify(generic_message)

        _check_rate_limit(f"resend:{email}", max_hits=3, window_seconds=3600)

        user = db.get_user_by_email(email)
        if user and not user["email_verified"]:
            token = _generate_token()
            expires_at = datetime.now(timezone.utc) + VERIFICATION_TOKEN_TTL
            db.set_verification_token(user["id"], token, expires_at)
            try:
                _send_verification_email(email, token)
            except Exception as exc:
                print(f"[auth] failed to resend verification email to {email}: {exc}")

        return jsonify(generic_message)

    @app.post("/api/auth/login")
    def login():
        """
        Log in with email and password.
        ---
        tags:
          - Auth
        summary: Log in
        responses:
          200:
            description: Logged in
          401:
            description: Invalid email or password
          403:
            description: Email not verified yet
          429:
            description: Too many attempts
        """
        _check_rate_limit(f"login:{client_ip()}", max_hits=10, window_seconds=300)

        payload = request.get_json(silent=True) or {}
        email = _normalize_email(payload.get("email", ""))
        password = payload.get("password") or ""

        user = db.get_user_by_email(email)
        password_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
        password_ok = check_password_hash(password_hash, password)

        if not user or not password_ok:
            return error_response(
                401, "Invalid credentials", "Invalid email or password."
            )

        if not user["email_verified"]:
            return error_response(
                403,
                "Email not verified",
                "Please verify your email before logging in.",
            )

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]

        return jsonify({"id": user["id"], "email": user["email"]})

    @app.post("/api/auth/logout")
    def logout():
        """
        Log out the current session.
        ---
        tags:
          - Auth
        summary: Log out
        responses:
          200:
            description: Logged out
        """
        session.clear()
        return jsonify({"message": "Logged out."})

    @app.get("/api/auth/me")
    def me():
        """
        Get the currently logged-in user, if any.
        ---
        tags:
          - Auth
        summary: Get current session user
        responses:
          200:
            description: >
              Always 200; check the "authenticated" field. "user" is null
              when not logged in.
        """
        user_id = session.get("user_id")
        user = db.get_user_by_id(user_id) if user_id else None

        if not user:
            session.clear()
            return jsonify({"authenticated": False, "user": None})

        return jsonify(
            {"authenticated": True, "user": {"id": user["id"], "email": user["email"]}}
        )
