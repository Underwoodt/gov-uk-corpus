"""Session + login-security tests (Postgres only).

Run everywhere: skipped unless the Postgres backend is configured.
    set -a; . ~/gov-uk-corpus.test.env; set +a; python -m unittest -v tests.test_sessions
They create a *fresh* `auth` schema and drop it after; if one already exists they skip
rather than risk clobbering it.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import accounts, sessions


@unittest.skipUnless(sessions._IS_PG, "Postgres backend required (set DB_BACKEND=postgres + DB_*)")
class TestSessions(unittest.TestCase):
    def setUp(self):
        from govuk_corpus.backend import db
        self.conn = db.connect()
        self._created = False
        try:
            self.conn.execute("CREATE SCHEMA auth")     # fresh only — no IF NOT EXISTS
            self.conn.commit()
            self._created = True
        except Exception:
            self.conn.rollback()
            self.conn.close()
            self.skipTest("an `auth` schema already exists — not clobbering it")
        accounts.init_auth_schema(self.conn)
        self.user = accounts.create_user(self.conn, email="tom@defra.gov.uk", first_name="Tom",
                                         last_name="U", password="a-good-password")

    def tearDown(self):
        if getattr(self, "_created", False):
            self.conn.execute("DROP SCHEMA auth CASCADE")
            self.conn.commit()
            self.conn.close()

    def _past(self, column):
        self.conn.execute(f"UPDATE auth.user_sessions SET {column} = now() - make_interval(days => 1)")
        self.conn.commit()

    # ---- sessions --------------------------------------------------------
    def test_create_and_resolve(self):
        cookie = sessions.create_session(self.conn, self.user["id"])
        self.assertIn(":", cookie)
        u = sessions.resolve(self.conn, cookie)
        self.assertIsNotNone(u)
        self.assertEqual(u["email"], "tom@defra.gov.uk")
        self.assertNotIn("password_hash", u)

    def test_wrong_secret_and_garbage(self):
        sid = sessions.create_session(self.conn, self.user["id"]).split(":", 1)[0]
        self.assertIsNone(sessions.resolve(self.conn, f"{sid}:wrong-secret"))
        self.assertIsNone(sessions.resolve(self.conn, "not-a-cookie"))
        self.assertIsNone(sessions.resolve(self.conn, ""))
        self.assertIsNone(sessions.resolve(self.conn, None))

    def test_revoke(self):
        cookie = sessions.create_session(self.conn, self.user["id"])
        sessions.revoke(self.conn, cookie)
        self.assertIsNone(sessions.resolve(self.conn, cookie))

    def test_expiry(self):
        cookie = sessions.create_session(self.conn, self.user["id"])
        self._past("expires_at")
        self.assertIsNone(sessions.resolve(self.conn, cookie))

    def test_idle_timeout(self):
        cookie = sessions.create_session(self.conn, self.user["id"])
        self._past("last_seen_at")
        self.assertIsNone(sessions.resolve(self.conn, cookie, idle_minutes=120))

    def test_disabled_account_invalidates_session(self):
        cookie = sessions.create_session(self.conn, self.user["id"])
        self.conn.execute("UPDATE auth.users SET account_status='disabled' WHERE id=%s", (self.user["id"],))
        self.conn.commit()
        self.assertIsNone(sessions.resolve(self.conn, cookie))

    def test_revoke_all_for_user(self):
        c1 = sessions.create_session(self.conn, self.user["id"])
        c2 = sessions.create_session(self.conn, self.user["id"])
        sessions.revoke_all_for_user(self.conn, self.user["id"])
        self.assertIsNone(sessions.resolve(self.conn, c1))
        self.assertIsNone(sessions.resolve(self.conn, c2))

    # ---- authentication + lockout ---------------------------------------
    def test_authenticate_success(self):
        u, reason = accounts.authenticate(self.conn, "TOM@defra.gov.uk", "a-good-password")
        self.assertIsNone(reason)
        self.assertIsNotNone(u)
        self.assertNotIn("password_hash", u)

    def test_authenticate_wrong_password_is_invalid(self):
        u, reason = accounts.authenticate(self.conn, "tom@defra.gov.uk", "nope")
        self.assertIsNone(u)
        self.assertEqual(reason, "invalid")

    def test_unknown_email_is_invalid_not_leaky(self):
        u, reason = accounts.authenticate(self.conn, "ghost@defra.gov.uk", "whatever")
        self.assertIsNone(u)
        self.assertEqual(reason, "invalid")   # same reason as wrong password (enumeration-safe)

    def test_lockout_after_repeated_failures(self):
        for _ in range(accounts.MAX_FAILED_LOGINS):
            accounts.authenticate(self.conn, "tom@defra.gov.uk", "wrong")
        # now locked, even with the correct password
        u, reason = accounts.authenticate(self.conn, "tom@defra.gov.uk", "a-good-password")
        self.assertIsNone(u)
        self.assertEqual(reason, "locked")

    def test_disabled_account_authenticate(self):
        self.conn.execute("UPDATE auth.users SET account_status='disabled' WHERE id=%s", (self.user["id"],))
        self.conn.commit()
        u, reason = accounts.authenticate(self.conn, "tom@defra.gov.uk", "a-good-password")
        self.assertIsNone(u)
        self.assertEqual(reason, "inactive")

    def test_audit_rows_written(self):
        accounts.authenticate(self.conn, "tom@defra.gov.uk", "a-good-password")
        accounts.authenticate(self.conn, "tom@defra.gov.uk", "wrong")
        events = [r["event"] for r in self.conn.execute("SELECT event FROM auth.audit_log ORDER BY id").fetchall()]
        self.assertIn("login_succeeded", events)
        self.assertIn("login_failed", events)


if __name__ == "__main__":
    unittest.main()
