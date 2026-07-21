import hashlib
import hmac
import json
import os
import unittest

from app import create_app, normalize_payload


class FakeSheetsClient:
    def __init__(self):
        self.records = []

    def append_records(self, records):
        self.records.extend(records)
        return {"inserted": len(records), "duplicates": 0}


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.copy()
        os.environ.update(
            {
                "META_VERIFY_TOKEN": "verify-test-token",
                "META_APP_SECRET": "app-test-secret",
                "MATON_API_KEY": "maton-test-token",
                "SHEET_ID": "sheet-test-id",
                "SHEET_TAB": "LEADS RAW - MESSAGING",
            }
        )
        self.fake = FakeSheetsClient()
        self.app = create_app(lambda: self.fake)
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_health_does_not_disclose_secrets(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["configured"]["meta_verify_token"])
        self.assertNotIn("verify-test-token", response.get_data(as_text=True))
        self.assertNotIn("app-test-secret", response.get_data(as_text=True))

    def test_meta_verification_success(self):
        response = self.client.get(
            "/webhook/meta?hub.mode=subscribe&hub.verify_token=verify-test-token&hub.challenge=123456"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "123456")

    def test_meta_verification_rejects_wrong_token(self):
        response = self.client.get(
            "/webhook/meta?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123456"
        )
        self.assertEqual(response.status_code, 403)

    def test_post_requires_valid_signature(self):
        payload = {"object": "page", "entry": []}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        response = self.client.post(
            "/webhook/meta",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=bad"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fake.records, [])

    def test_signed_whatsapp_payload_is_written(self):
        payload = whatsapp_payload()
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"app-test-secret", raw, hashlib.sha256
        ).hexdigest()
        response = self.client.post(
            "/webhook/meta",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": signature},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["inserted"], 1)
        self.assertEqual(len(self.fake.records), 1)
        self.assertEqual(self.fake.records[0]["channel"], "whatsapp")
        self.assertEqual(self.fake.records[0]["phone"], "+15551234567")


class NormalizationTests(unittest.TestCase):
    def test_whatsapp_message_fields(self):
        records = normalize_payload(whatsapp_payload())
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["channel"], "whatsapp")
        self.assertEqual(record["profile_name"], "Jane Driver")
        self.assertEqual(record["message_id"], "wamid.TEST")
        self.assertEqual(record["message_text"], "I need an APU quote")
        self.assertEqual(record["ad_id"], "238500001")
        self.assertEqual(record["contact_channel_id"], "15551234567")

    def test_messenger_message_fields(self):
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "544889629006881",
                    "time": 1784600000000,
                    "messaging": [
                        {
                            "sender": {"id": "PSID-123"},
                            "recipient": {"id": "544889629006881"},
                            "timestamp": 1784600000000,
                            "message": {
                                "mid": "m_TEST",
                                "text": "How much is the APU?",
                                "referral": {
                                    "source": "ADS",
                                    "type": "OPEN_THREAD",
                                    "ad_id": "12000000001",
                                    "ref": "summer_apu_campaign",
                                },
                            },
                        }
                    ],
                }
            ],
        }
        records = normalize_payload(payload)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["channel"], "messenger")
        self.assertEqual(record["page_or_phone_id"], "544889629006881")
        self.assertEqual(record["contact_channel_id"], "PSID-123")
        self.assertEqual(record["ad_id"], "12000000001")
        self.assertEqual(record["lead_status"], "Nuevo lead — datos incompletos")


def whatsapp_payload():
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA-123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": "PHONE-ID-1",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Jane Driver"},
                                    "wa_id": "15551234567",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.TEST",
                                    "timestamp": "1784600000",
                                    "type": "text",
                                    "text": {"body": "I need an APU quote"},
                                    "referral": {
                                        "source_type": "ad",
                                        "source_id": "238500001",
                                        "source_url": "https://fb.me/example",
                                        "headline": "Go Green APU",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
