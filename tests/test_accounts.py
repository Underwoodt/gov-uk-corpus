"""Account/auth tests.

Pure tests (domain validation, Argon2id hashing) run everywhere. The DB tests run
ONLY when the Postgres backend is configured (set DB_BACKEND=postgres + DB_* env),
and they create a *fresh* `auth` schema — if one already exists they skip rather
than risk clobbering it.

Run pure tests:   python3 -m unittest -v tests.test_accounts
Run all (PG):     set -a; . ~/gov-uk-corpus.test.env; set +a; python -m unittest -v tests.test_accounts
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import accounts

try:
    from argon2 import PasswordHasher  # noqa: F401
    _HAS_ARGON2 = True
except Exception:
    _HAS_ARGON2 = False


class TestDomainValidation(unittest.TestCase):
    def test_allowed_domains_case_insensitive(self):
        for e in ("tom@defra.gov.uk", "Tom.Jones@DEFRA.GOV.UK", "a@equalexperts.com", "A@EqualExperts.com"):
            self.assertTrue(accounts.email_domain_allowed(e), e)

    def test_rejected(self):
        for e in ("tom@gmail.com", "tom@defra.gov.uk.evil.com", "tom@sub.defra.gov.uk",
                  "tom@equalexperts.co.uk", "not-an-email", "@defra.gov.uk", "tom@", ""):
            self.assertFalse(accounts.email_domain_allowed(e), e)

    def test_normalise(self):
        self.assertEqual(accounts.normalise_email("  Tom@Defra.GOV.uk "), "tom@defra.gov.uk")


@unittest.skipUnless(_HAS_ARGON2, "argon2-cffi not installed")
class TestHashing(unittest.TestCase):
    def test_hash_verify_roundtrip(self):
        h = accounts.hash_password("correct horse battery staple")
        self.assertTrue(h.startswith("$argon2id$"))
        self.assertTrue(accounts.verify_password(h, "correct horse battery staple"))
        self.assertFalse(accounts.verify_password(h, "wrong password"))

    def test_unique_salt_per_password(self):
        a = accounts.hash_password("same-password")
        b = accounts.hash_password("same-password")
        self.assertNotEqual(a, b)                       # different salts -> different hashes
        self.assertTrue(accounts.verify_password(a, "same-password"))
        self.assertTrue(accounts.verify_password(b, "same-password"))

    def test_needs_rehash_false_for_fresh_hash(self):
        self.assertFalse(accounts.needs_rehash(accounts.hash_password("x" * 12)))

    def test_verify_bad_hash_is_false_not_raise(self):
        self.assertFalse(accounts.verify_password("not-a-hash", "x"))


@unittest.skipUnless(accounts._IS_PG, "Postgres backend required (set DB_BACKEND=postgres + DB_*)")
class TestAccountsDB(unittest.TestCase):
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

    def tearDown(self):
        if getattr(self, "_created", False):
            self.conn.execute("DROP SCHEMA auth CASCADE")
            self.conn.commit()
            self.conn.close()

    def test_create_returns_public_row_without_hash(self):
        u = accounts.create_user(self.conn, email="Tom@defra.gov.uk", first_name="Tom",
                                 last_name="Underwood", password="a-good-password")
        self.assertEqual(u["email"], "tom@defra.gov.uk")   # normalised
        self.assertEqual(u["role"], "User")
        self.assertEqual(u["account_status"], "active")
        self.assertNotIn("password_hash", u)               # never returned

    def test_fetch_case_insensitive_and_verify(self):
        accounts.create_user(self.conn, email="jane@equalexperts.com", first_name="Jane",
                             last_name="Smith", password="another-good-one")
        got = accounts.get_user_by_email(self.conn, "JANE@EqualExperts.com", with_hash=True)
        self.assertIsNotNone(got)
        self.assertTrue(accounts.verify_password(got["password_hash"], "another-good-one"))
        self.assertIsNone(accounts.get_user_by_email(self.conn, "nobody@defra.gov.uk"))

    def test_duplicate_email_rejected(self):
        accounts.create_user(self.conn, email="dup@defra.gov.uk", first_name="A", last_name="B",
                             password="password-one")
        with self.assertRaises(accounts.EmailTakenError):
            accounts.create_user(self.conn, email="DUP@defra.gov.uk", first_name="C", last_name="D",
                                 password="password-two")

    def test_bad_domain_rejected_server_side(self):
        with self.assertRaises(ValueError):
            accounts.create_user(self.conn, email="tom@gmail.com", first_name="T", last_name="U",
                                 password="password-xyz")


if __name__ == "__main__":
    unittest.main()
