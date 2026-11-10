"""Reusable HTTP login helper owned by legacy business API tests.

Kept separate from auth_test.py so the latter can live in Platform-only tests
without forcing other legacy modules to import a test module.
"""


def login(client, password="test-password-123", username="admin"):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
