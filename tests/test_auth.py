from tests.helpers import make_user


def test_register_login_and_me(client):
    creds = {"email": "Riya@Example.com", "password": "password123"}
    r = client.post("/auth/register", json=creds)
    assert r.status_code == 201
    assert r.json()["email"] == "riya@example.com"  # normalised
    assert r.json()["is_admin"] is False
    assert "password" not in r.text

    token = client.post("/auth/login", json=creds).json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "riya@example.com"


def test_duplicate_email_rejected(client):
    creds = {"email": "dup@example.com", "password": "password123"}
    assert client.post("/auth/register", json=creds).status_code == 201
    assert client.post("/auth/register", json=creds).status_code == 409


def test_wrong_password_and_unknown_user_look_the_same(client):
    client.post("/auth/register", json={"email": "a@example.com", "password": "password123"})
    wrong = client.post("/auth/login", json={"email": "a@example.com", "password": "nope-nope-nope"})
    unknown = client.post("/auth/login", json={"email": "z@example.com", "password": "password123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/auth/me").status_code == 401
    bad = client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


def test_only_admins_create_events(client):
    user = make_user(client)
    r = client.post(
        "/events",
        headers=user["headers"],
        json={
            "name": "x",
            "venue": "y",
            "starts_at": "2030-01-01T00:00:00Z",
            "rows": 1,
            "seats_per_row": 1,
            "price": "10.00",
        },
    )
    assert r.status_code == 403


def test_input_validation(client):
    r = client.post("/auth/register", json={"email": "not-an-email", "password": "short"})
    assert r.status_code == 422
