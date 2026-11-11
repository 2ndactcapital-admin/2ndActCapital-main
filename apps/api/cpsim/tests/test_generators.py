"""cpsim01 Task 4 — the generators provably never produce a real value (pure; no database, no network).

    cd apps/api && venv/bin/python -m pytest -q cpsim/tests        (or: python -m unittest cpsim.tests.test_generators)
"""

from __future__ import annotations

import random
import re
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from cpsim import generators as G  # noqa: E402
from cpsim import mirror as M  # noqa: E402

REAL_ABAS = ("026009593", "021000021", "011000138", "121000358", "322271627")   # published routing numbers
REAL_IBANS = ("GB82WEST12345698765432", "DE89370400440532013000", "FR1420041010050500013M02606")


class Generators(unittest.TestCase):
    def test_real_checksums_recognised(self):
        self.assertTrue(all(G.aba_checksum_ok(a) for a in REAL_ABAS))
        self.assertTrue(all(G.iban_ok(i) for i in REAL_IBANS))

    def test_aba_always_fails_the_real_checksum(self):
        rng = random.Random(20261110)
        for _ in range(1000):
            a = G.fake_aba(rng)
            self.assertRegex(a, r"^99[0-9]{7}$")
            self.assertFalse(G.aba_checksum_ok(a), a)

    def test_aba_every_wrong_digit_reachable(self):
        rng = random.Random(7)
        self.assertEqual(len({G.fake_aba(rng)[-1] for _ in range(2000)}), 10)

    def test_iban_invalid_by_construction(self):
        rng = random.Random(3)
        for _ in range(1000):
            i = G.fake_iban(rng)
            self.assertIn(i[2:4], G.INVALID_IBAN_CHECKS)
            self.assertFalse(G.iban_ok(i), i)

    def test_bic_cannot_be_real(self):
        for _ in range(200):
            b = G.fake_bic()
            self.assertRegex(b, r"^[A-Z]{4}XX[A-Z0-9]0([A-Z0-9]{3})?$")

    def test_account_number(self):
        self.assertRegex(G.fake_account_number(), r"^CPSIM[0-9]{10}$")

    def test_secret_prefix_hash_last4(self):
        k = G.new_api_key()
        self.assertTrue(k["value"].startswith("cpsim_live_"))
        self.assertEqual(k["last4"], k["value"][-4:])
        self.assertNotIn(k["value"], k["hash"] + k["salt"])
        self.assertTrue(G.matches(k["salt"], k["hash"], k["value"]))
        self.assertFalse(G.matches(k["salt"], k["hash"], k["value"] + "x"))
        self.assertTrue(G.new_fix_password()["value"].startswith("cpsim_fixpw_"))
        self.assertFalse(G.is_simulator_secret("sk_live_abc"))
        with self.assertRaises(ValueError):
            G.new_secret("sk_live_")

    def test_bank_details_shapes(self):
        d = G.fake_bank_details("domestic", "Test Bank")
        self.assertFalse(G.aba_checksum_ok(d["aba_wire"]) or G.aba_checksum_ok(d["aba_ach"]))
        i = G.fake_bank_details("international", "Test Bank")
        self.assertFalse(G.iban_ok(i["iban"]))
        self.assertTrue(i["bank_name"].startswith("CPSIM "))


class Envelope(unittest.TestCase):
    def setUp(self):
        self.schema = M.load_schema("generic_envelope_v1")

    def test_valid(self):
        doc = {"message_type": "test.ping", "message_id": "m-1", "as_of": "2026-11-10T14:00:00Z", "payload": {}}
        self.assertEqual(M.validate(self.schema, doc), [])

    def test_malformed(self):
        errs = M.validate(self.schema, {"message_type": "X", "as_of": "yesterday", "payload": [], "extra": 1})
        paths = {e["path"] for e in errs}
        self.assertTrue({"$.message_id", "$.message_type", "$.as_of", "$.payload", "$.extra"} <= paths, errs)

    def test_mirror_uses_counterparty_clock(self):
        from datetime import date, datetime, timezone
        M.set_now(lambda: datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc))
        try:
            doc = {"message_type": "test.ping", "message_id": "m-1", "as_of": "2026-11-10T14:00:00Z", "payload": {"a": 1}}
            out = M.mirror(doc, "x", date(2026, 11, 13))
        finally:
            M.set_now(None)
        self.assertEqual(out["message_id"], "m-1")
        self.assertTrue(out["mirrored"])
        self.assertTrue(out["received_at"].startswith("2026-11-13T15:30"))
        self.assertTrue(re.match(r".*Z$", out["received_at"]))


if __name__ == "__main__":
    unittest.main()
