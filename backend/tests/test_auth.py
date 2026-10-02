from sqlalchemy import select

from tunnelui.models import AdminSession


def test_cookie_flags_and_authentication(http):
    assert http.get("/api/clients").status_code == 401
    response = http.get("/api/auth/csrf")
    cookie = response.headers["set-cookie"].lower()
    assert all(value in cookie for value in ("httponly", "secure", "samesite=strict"))
    assert response.headers["cache-control"] == "no-store"


def test_csrf_and_origin_required(http):
    token = http.get("/api/auth/csrf").json()["csrf_token"]
    body = {"username": "admin", "password": "test-only-password"}
    assert http.post("/api/auth/login", json=body).status_code == 403
    assert http.post("/api/auth/login", json=body, headers={
        "X-CSRF-Token": token, "Origin": "https://evil.example"
    }).status_code == 403


def test_session_rotation_and_logout(http, app):
    token = http.get("/api/auth/csrf").json()["csrf_token"]
    old_cookie = http.cookies.get("tunnelui_session")
    response = http.post("/api/auth/login", json={"username": "admin", "password": "test-only-password"},
                         headers={"Origin": "https://testserver", "X-CSRF-Token": token})
    assert response.status_code == 200
    assert http.cookies.get("tunnelui_session") != old_cookie
    with app.state.sessions() as db:
        sessions = db.scalars(select(AdminSession)).all()
        assert len(sessions) == 1
        assert sessions[0].token_hash != http.cookies.get("tunnelui_session")
    assert http.get("/api/auth/me").json() == {"username": "admin"}
    http.headers.update({"Origin": "https://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
    assert http.post("/api/auth/logout").status_code == 204
    assert http.get("/api/auth/me").status_code == 401


def test_rate_limit(http):
    token = http.get("/api/auth/csrf").json()["csrf_token"]
    for _ in range(5):
        assert http.post("/api/auth/login", json={"username": "nobody", "password": "wrong"},
                         headers={"Origin": "https://testserver", "X-CSRF-Token": token}).status_code == 401
    assert http.post("/api/auth/login", json={"username": "admin", "password": "wrong"},
                     headers={"Origin": "https://testserver", "X-CSRF-Token": token}).status_code == 429


def test_validation_does_not_echo_password(http):
    token = http.get("/api/auth/csrf").json()["csrf_token"]
    secret = "LEAK-MARKER" * 200
    response = http.post("/api/auth/login", json={"username": "admin", "password": secret},
                         headers={"Origin": "https://testserver", "X-CSRF-Token": token})
    assert response.status_code == 422
    assert "LEAK-MARKER" not in response.text


def test_expired_session(authenticated, app):
    with app.state.sessions() as db:
        row = db.scalar(select(AdminSession))
        row.expires_at = 0
        db.commit()
    assert authenticated.get("/api/clients").status_code == 401
