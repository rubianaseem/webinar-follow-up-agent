import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webinar_agent as wa  # noqa: E402


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.res = wa.run(ROOT / "sample_registrants.csv", ROOT / "sample_attendance.csv",
                         ROOT / "sample_crm_contacts.csv", ROOT / "webinar_policy.json", cls.out, use_claude=False)
        cls.by = {p.email: p for p in cls.res["people"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_duplicate_registrations_merge(self):
        p = self.by["priya.raman@northwind-logistics.com"]
        self.assertEqual(p.duplicate_registrations, 1)
        self.assertEqual(p.registered_at, "2026-10-20 09:14")

    def test_rejoins_are_summed(self):
        p = self.by["priya.raman@northwind-logistics.com"]
        self.assertEqual(p.minutes, 41)
        self.assertEqual(p.sessions, 2)

    def test_hot_lead_gets_call_and_opener_from_question(self):
        p = self.by["priya.raman@northwind-logistics.com"]
        self.assertEqual(p.segment, wa.HOT_CALL)
        self.assertIn("Salesforce round robin", p.opener)
        self.assertEqual(p.call_by, "2026-10-29 15:45")

    def test_open_deal_goes_to_owner_not_sdr(self):
        self.assertEqual(self.by["j.okafor@brightwave.io"].segment, wa.AE_HEADS_UP)

    def test_opt_out_beats_high_score(self):
        p = self.by["dana.white@medcorehealth.com"]
        self.assertGreaterEqual(p.score, 70)
        self.assertEqual(p.segment, wa.SUPPRESS)
        self.assertIn("OPTED_OUT_BUT_ASKED", [r["reason"] for r in self.res["review"]])

    def test_different_email_matched_but_held(self):
        p = self.by["marco.bianchi@ledgerly.com"]
        self.assertEqual(p.matched_by, "name+company")
        self.assertNotIn(p, self.res["importable"])

    def test_competitor_and_internal_quarantined(self):
        self.assertEqual(self.by["alex@routeiq.ai"].segment, wa.QUARANTINE)
        self.assertEqual(self.by["host@yourco.com"].segment, wa.QUARANTINE)
        self.assertIn("MINUTES_CAPPED", self.by["host@yourco.com"].flags)

    def test_replay_and_personal_email(self):
        p = self.by["sam.lee@gmail.com"]
        self.assertEqual(p.session_type, "replay")
        self.assertEqual(p.segment, wa.LOW)

    def test_no_show_at_customer_held_for_owner(self):
        p = self.by["elena.costa@quillstack.com"]
        self.assertEqual(p.segment, wa.NO_SHOW)
        self.assertIn("CUSTOMER_ACCOUNT", p.flags)

    def test_outputs_written(self):
        for name in ("hubspot_webinar_import.csv", "sdr_call_list.csv", "review_queue.csv", "quarantine.csv",
                     "follow_up_plan.csv", "follow_up_emails.md", "webinar_brief.md"):
            self.assertTrue((self.out / name).exists(), name)
        with open(self.out / "hubspot_webinar_import.csv") as fh:
            emails = [r["Email"] for r in csv.DictReader(fh)]
        self.assertNotIn("alex@routeiq.ai", emails)


class Rules(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "webinar_policy.json").read_text())

    def test_hot_without_consent_goes_to_review(self):
        p = wa.Person(email="x@acme.com", first_name="X", job_title="VP Sales", attended=True,
                      session_type="live", pct_watched=100, poll_answer="Evaluating", questions=["pricing?"])
        wa.score(p, self.policy)
        review = []
        wa.segment(p, self.policy, review, wa.datetime(2026, 1, 1))
        self.assertEqual(p.segment, wa.HOT_CALL)
        self.assertEqual(review[0]["reason"], "HOT_WITHOUT_CONSENT")

    def test_test_entry_quarantined(self):
        p = wa.Person(email="test@test.com")
        wa.segment(p, self.policy, [], wa.datetime(2026, 1, 1))
        self.assertEqual(p.segment, wa.QUARANTINE)

    def test_header_aliases(self):
        self.assertEqual(wa.pick({"Email Address": "a@b.com"}, "Email", "Email Address"), "a@b.com")


if __name__ == "__main__":
    unittest.main()
