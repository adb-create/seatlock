"""Race test: N users try to book the SAME last seat at the SAME moment.

Start the API first (docker compose up), seed the admin (python -m scripts.seed),
then run:

    python -m scripts.load_test --users 500

Expected result: exactly 1 x 201 Created, the rest 409 Conflict, and the seat
map shows the seat held by one booking. Paste your numbers into the README.
"""

import argparse
import asyncio
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@seatlock.dev")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin12345")
USER_PASSWORD = "loadtest-password"


async def get_token(client: httpx.AsyncClient, email: str, password: str, register: bool) -> str:
    creds = {"email": email, "password": password}
    if register:
        r = await client.post("/auth/register", json=creds)
        if r.status_code not in (201, 409):
            r.raise_for_status()
    r = await client.post("/auth/login", json=creds)
    r.raise_for_status()
    return r.json()["access_token"]


async def main(base_url: str, n_users: int) -> None:
    limits = httpx.Limits(max_connections=n_users + 10, max_keepalive_connections=n_users + 10)
    async with httpx.AsyncClient(base_url=base_url, timeout=120, limits=limits) as client:
        admin_token = await get_token(client, ADMIN_EMAIL, ADMIN_PASSWORD, register=False)
        r = await client.post(
            "/events",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": f"Load test {uuid.uuid4().hex[:6]}",
                "venue": "Race Arena",
                "starts_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "rows": 1,
                "seats_per_row": 1,  # exactly ONE seat for everybody to fight over
                "price": "999.00",
            },
        )
        r.raise_for_status()
        event_id = r.json()["id"]
        seat_id = (await client.get(f"/events/{event_id}/seats")).json()[0]["id"]

        # Setup (not timed): users are reused across runs.
        print(f"Preparing {n_users} users (password hashing makes this the slow part)...")
        sem = asyncio.Semaphore(25)

        async def prepare(i: int) -> str:
            async with sem:
                return await get_token(client, f"loaduser{i}@seatlock.dev", USER_PASSWORD, register=True)

        tokens = await asyncio.gather(*(prepare(i) for i in range(n_users)))

        # The race: everyone fires at once.
        start_gate = asyncio.Event()

        async def attempt(token: str) -> tuple[int, float]:
            await start_gate.wait()
            t0 = time.perf_counter()
            resp = await client.post(
                "/bookings",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
                json={"event_id": event_id, "seat_ids": [seat_id]},
            )
            return resp.status_code, (time.perf_counter() - t0) * 1000

        tasks = [asyncio.create_task(attempt(t)) for t in tokens]
        await asyncio.sleep(0.2)
        print(f"Firing {n_users} concurrent booking requests for seat #{seat_id}...")
        wall0 = time.perf_counter()
        start_gate.set()
        results = await asyncio.gather(*tasks)
        wall_ms = (time.perf_counter() - wall0) * 1000

        codes: dict[int, int] = {}
        for code, _ in results:
            codes[code] = codes.get(code, 0) + 1
        latencies = sorted(ms for _, ms in results)
        p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]

        seat = (await client.get(f"/events/{event_id}/seats")).json()[0]

        print("\n=== Result ===")
        print(f"Requests        : {n_users}")
        for code in sorted(codes):
            print(f"HTTP {code}        : {codes[code]}")
        print(f"Wall time       : {wall_ms:.0f} ms")
        print(f"Latency p50     : {statistics.median(latencies):.0f} ms")
        print(f"Latency p95     : {p95:.0f} ms")
        print(f"Seat status now : {seat['status']}")
        ok = codes.get(201, 0) == 1 and codes.get(409, 0) == n_users - 1
        print("\nPASS: exactly one winner, zero double bookings" if ok else "\nCHECK THE RESULTS ABOVE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--users", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.users))
