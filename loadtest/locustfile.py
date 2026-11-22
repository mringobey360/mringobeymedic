"""
Load test for SmartPOS (Locust). NOT part of the deployed app.

Simulates a logged-in pharmacy user: the bell poll, opening the dashboard / POS /
products pages, and (for a share of users) an open chat thread polling for messages.

Run against a STAGING copy with test accounts - never against production data:

    pip install locust
    export LT_USERNAME=loadtest_user LT_PASSWORD='...'      # a real test account
    export LT_THREAD_ID=1                                    # optional: chat thread that account can open
    locust -f loadtest/locustfile.py --host https://your-staging.up.railway.app

Open http://localhost:8089, start with 50 users (spawn rate 5/s), then step up. Watch p95 response
time and error rate in Locust and CPU / RAM in the Railway dashboard. The system is "full" when p95
climbs above ~1s or errors appear - that number is your real capacity.
"""
import os
import re

from locust import HttpUser, between, task

USERNAME = os.environ.get("LT_USERNAME", "")
PASSWORD = os.environ.get("LT_PASSWORD", "")
THREAD_ID = os.environ.get("LT_THREAD_ID", "")


class PharmacyUser(HttpUser):
    wait_time = between(4, 12)          # think time between page views

    def on_start(self):
        page = self.client.get("/accounts/login/", name="login page")
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        token = match.group(1) if match else ""
        self.client.post(
            "/accounts/login/",
            {"username": USERNAME, "password": PASSWORD, "csrfmiddlewaretoken": token},
            headers={"Referer": self.host + "/accounts/login/"},
            name="login submit",
        )
        self.last_id = 0

    # The browser polls the bell every ~15 s.
    @task(6)
    def bell_poll(self):
        self.client.get("/notifications/api/feed/", name="bell feed")

    # Chat page open: one feed poll every ~2.5 s (only if LT_THREAD_ID is set).
    @task(4)
    def chat_poll(self):
        if THREAD_ID:
            self.client.get(
                f"/chat/{THREAD_ID}/api/messages/?after_id={self.last_id}",
                name="chat feed",
            )

    @task(2)
    def dashboard(self):
        self.client.get("/", name="dashboard")

    @task(2)
    def pos(self):
        self.client.get("/sales/pos/", name="pos page")

    @task(1)
    def products(self):
        self.client.get("/inventory/", name="inventory")
