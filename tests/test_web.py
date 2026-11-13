from tests.helpers import key, make_user


def test_home_page_serves_the_web_app(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/static/app.js" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_premium_rows_get_premium_price_and_events_show_price_range(client):
    admin = make_user(client, admin=True)
    r = client.post(
        "/events",
        headers=admin["headers"],
        json={
            "name": "Tiered Show",
            "venue": "Hall",
            "starts_at": "2030-01-01T18:00:00+05:30",
            "rows": 3,
            "seats_per_row": 2,
            "price": "500.00",
            "premium_rows": 1,
            "premium_price": "1200.00",
        },
    )
    assert r.status_code == 201, r.text
    event = r.json()
    assert event["min_price"] == "500.00"
    assert event["max_price"] == "1200.00"

    seats = client.get(f"/events/{event['id']}/seats").json()
    prices = {s["label"]: s["price"] for s in seats}
    assert prices["A1"] == prices["A2"] == "1200.00"
    assert prices["B1"] == prices["C2"] == "500.00"


def test_premium_rows_validation(client):
    admin = make_user(client, admin=True)
    base = {
        "name": "x",
        "venue": "y",
        "starts_at": "2030-01-01T00:00:00Z",
        "rows": 2,
        "seats_per_row": 2,
        "price": "100.00",
    }
    too_many = {**base, "premium_rows": 3, "premium_price": "200.00"}
    no_price = {**base, "premium_rows": 1}
    assert client.post("/events", headers=admin["headers"], json=too_many).status_code == 422
    assert client.post("/events", headers=admin["headers"], json=no_price).status_code == 422


def test_bookings_include_event_name_and_time(client):
    admin = make_user(client, admin=True)
    event = client.post(
        "/events",
        headers=admin["headers"],
        json={
            "name": "Named Show",
            "venue": "Hall",
            "starts_at": "2030-01-01T18:00:00+05:30",
            "rows": 1,
            "seats_per_row": 1,
            "price": "100.00",
        },
    ).json()
    seat_id = client.get(f"/events/{event['id']}/seats").json()[0]["id"]
    user = make_user(client)
    r = client.post(
        "/bookings",
        headers={**user["headers"], "Idempotency-Key": key()},
        json={"event_id": event["id"], "seat_ids": [seat_id]},
    )
    assert r.status_code == 201
    assert r.json()["event_name"] == "Named Show"
    assert r.json()["event_starts_at"].startswith("2030-01-01")
