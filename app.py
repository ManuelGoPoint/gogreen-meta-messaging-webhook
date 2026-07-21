import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request


LOGGER = logging.getLogger("gogreen_meta_webhook")
SHEET_HEADERS = [
    "received_at",
    "event_timestamp",
    "channel",
    "source_object",
    "page_or_phone_id",
    "contact_channel_id",
    "profile_name",
    "phone",
    "email",
    "message_id",
    "message_type",
    "message_text",
    "referral_source",
    "campaign_id",
    "ad_id",
    "ad_name",
    "conversation_id",
    "lead_status",
    "first_interaction_at",
    "last_interaction_at",
    "processing_status",
    "delivery_attempts",
    "last_error",
    "raw_payload_hash",
    "raw_payload_json",
    "inserted_at",
    "updated_at",
    "notes",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_timestamp(value, milliseconds=False):
    if value in (None, ""):
        return ""
    try:
        number = float(value)
        if milliseconds or number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def compact_json(value, limit=45_000):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text) > limit:
        return text[: limit - 22] + '..."[TRUNCATED]"'
    return text


def payload_hash(value):
    return hashlib.sha256(compact_json(value, limit=1_000_000).encode("utf-8")).hexdigest()


def normalize_phone(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return f"+{digits}" if digits else ""


def deterministic_event_id(event):
    return "synthetic-" + payload_hash(event)[:32]


def whatsapp_message_text(message):
    message_type = message.get("type", "unknown")
    if message_type == "text":
        return (message.get("text") or {}).get("body", "")
    if message_type == "button":
        button = message.get("button") or {}
        return button.get("text") or button.get("payload") or ""
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or reply.get("description") or reply.get("id") or ""
    if message_type in {"image", "video", "document", "audio", "sticker"}:
        media = message.get(message_type) or {}
        return media.get("caption") or f"[{message_type}]"
    if message_type == "reaction":
        return (message.get("reaction") or {}).get("emoji", "[reaction]")
    if message_type == "location":
        location = message.get("location") or {}
        return compact_json(
            {key: location.get(key) for key in ("name", "address", "latitude", "longitude")}
        )
    return f"[{message_type}]"


def messenger_message_text(event):
    message = event.get("message") or {}
    if message.get("text"):
        return message["text"]
    attachments = message.get("attachments") or []
    if attachments:
        return " ".join(f"[{item.get('type', 'attachment')}]" for item in attachments)
    postback = event.get("postback") or {}
    if postback:
        return postback.get("title") or postback.get("payload") or "[postback]"
    referral = event.get("referral") or message.get("referral") or {}
    if referral:
        return referral.get("ref") or "[referral]"
    return ""


def normalize_payload(payload):
    if not isinstance(payload, dict):
        return []
    object_type = payload.get("object", "")
    if object_type == "whatsapp_business_account":
        return normalize_whatsapp(payload)
    if object_type == "page":
        return normalize_messenger(payload)
    return []


def normalize_whatsapp(payload):
    records = []
    received_at = utc_now()
    for entry in payload.get("entry") or []:
        source_id = str(entry.get("id", ""))
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            page_or_phone_id = str(metadata.get("phone_number_id") or source_id)
            contacts = {
                str(contact.get("wa_id", "")): contact
                for contact in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                sender = str(message.get("from", ""))
                contact = contacts.get(sender, {})
                referral = message.get("referral") or {}
                event_timestamp = iso_timestamp(message.get("timestamp")) or received_at
                raw_event = {
                    "object": payload.get("object"),
                    "entry_id": source_id,
                    "metadata": metadata,
                    "contact": contact,
                    "message": message,
                }
                record = base_record(received_at, event_timestamp, raw_event)
                record.update(
                    {
                        "channel": "whatsapp",
                        "source_object": object_type(payload),
                        "page_or_phone_id": page_or_phone_id,
                        "contact_channel_id": sender,
                        "profile_name": ((contact.get("profile") or {}).get("name", "")),
                        "phone": normalize_phone(sender),
                        "message_id": str(message.get("id") or deterministic_event_id(raw_event)),
                        "message_type": str(message.get("type", "unknown")),
                        "message_text": whatsapp_message_text(message),
                        "referral_source": compact_json(referral) if referral else "",
                        "campaign_id": "",
                        "ad_id": str(
                            referral.get("source_id", "")
                            if referral.get("source_type") == "ad"
                            else ""
                        ),
                        "ad_name": str(referral.get("headline", "")),
                        "conversation_id": f"{page_or_phone_id}:{sender}",
                        "lead_status": "Nuevo lead",
                    }
                )
                records.append(finalize_record(record))
    return records


def normalize_messenger(payload):
    records = []
    received_at = utc_now()
    for entry in payload.get("entry") or []:
        page_id = str(entry.get("id", ""))
        for event in entry.get("messaging") or []:
            message = event.get("message") or {}
            if message.get("is_echo"):
                continue
            if not (message or event.get("postback") or event.get("referral")):
                continue
            sender_id = str((event.get("sender") or {}).get("id", ""))
            referral = event.get("referral") or message.get("referral") or {}
            event_timestamp = iso_timestamp(event.get("timestamp"), milliseconds=True) or received_at
            message_type = "text" if message.get("text") else "attachment" if message.get("attachments") else "postback" if event.get("postback") else "referral"
            raw_event = {
                "object": payload.get("object"),
                "page_id": page_id,
                "event": event,
            }
            record = base_record(received_at, event_timestamp, raw_event)
            record.update(
                {
                    "channel": "messenger",
                    "source_object": object_type(payload),
                    "page_or_phone_id": page_id,
                    "contact_channel_id": sender_id,
                    "message_id": str(message.get("mid") or deterministic_event_id(raw_event)),
                    "message_type": message_type,
                    "message_text": messenger_message_text(event),
                    "referral_source": compact_json(referral) if referral else "",
                    "campaign_id": "",
                    "ad_id": str(referral.get("ad_id", "")),
                    "ad_name": "",
                    "conversation_id": f"{page_id}:{sender_id}",
                    "lead_status": "Nuevo lead — datos incompletos",
                }
            )
            records.append(finalize_record(record))
    return records


def object_type(payload):
    return str(payload.get("object", ""))


def base_record(received_at, event_timestamp, raw_event):
    return {
        "received_at": received_at,
        "event_timestamp": event_timestamp,
        "channel": "",
        "source_object": "",
        "page_or_phone_id": "",
        "contact_channel_id": "",
        "profile_name": "",
        "phone": "",
        "email": "",
        "message_id": "",
        "message_type": "",
        "message_text": "",
        "referral_source": "",
        "campaign_id": "",
        "ad_id": "",
        "ad_name": "",
        "conversation_id": "",
        "lead_status": "",
        "first_interaction_at": event_timestamp,
        "last_interaction_at": event_timestamp,
        "processing_status": "stored",
        "delivery_attempts": 1,
        "last_error": "",
        "raw_payload_hash": payload_hash(raw_event),
        "raw_payload_json": compact_json(raw_event),
        "inserted_at": received_at,
        "updated_at": received_at,
        "notes": "",
    }


def finalize_record(record):
    for header in SHEET_HEADERS:
        record.setdefault(header, "")
    return record


def valid_signature(raw_body, signature_header, app_secret):
    if not app_secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class MatonSheetsClient:
    def __init__(self):
        self.api_key = os.environ.get("MATON_API_KEY", "")
        self.sheet_id = os.environ.get("SHEET_ID", "")
        self.sheet_tab = os.environ.get("SHEET_TAB", "LEADS RAW - MESSAGING")
        if not self.api_key or not self.sheet_id:
            raise RuntimeError("Sheets integration is not configured")
        self.base_url = (
            "https://gateway.maton.ai/google-sheets/v4/spreadsheets/" + self.sheet_id
        )

    def _request(self, method, url, body=None, attempts=3):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "GoGreen-Meta-Webhook/1.0",
        }
        last_error = None
        for attempt in range(attempts):
            try:
                request_object = urllib.request.Request(
                    url, data=data, headers=headers, method=method
                )
                with urllib.request.urlopen(request_object, timeout=25) as response:
                    content = response.read()
                    return json.loads(content) if content else {}
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in {429, 500, 502, 503, 504}:
                    raise
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
        raise RuntimeError("Sheets request failed after retries") from last_error

    def existing_message_ids(self):
        sheet_range = f"'{self.sheet_tab}'!J2:J"
        encoded_range = urllib.parse.quote(sheet_range, safe="")
        data = self._request("GET", f"{self.base_url}/values/{encoded_range}")
        return {
            str(row[0])
            for row in data.get("values", [])
            if row and str(row[0]).strip()
        }

    def append_records(self, records):
        existing = self.existing_message_ids()
        seen = set()
        unique = []
        for record in records:
            message_id = str(record.get("message_id", ""))
            if message_id in existing or message_id in seen:
                continue
            seen.add(message_id)
            unique.append(record)
        duplicate_count = len(records) - len(unique)
        if not unique:
            return {"inserted": 0, "duplicates": duplicate_count}
        sheet_range = f"'{self.sheet_tab}'!A:AB"
        encoded_range = urllib.parse.quote(sheet_range, safe="")
        query = urllib.parse.urlencode(
            {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
        )
        rows = [[record.get(header, "") for header in SHEET_HEADERS] for record in unique]
        self._request(
            "POST",
            f"{self.base_url}/values/{encoded_range}:append?{query}",
            {"majorDimension": "ROWS", "values": rows},
        )
        return {"inserted": len(unique), "duplicates": duplicate_count}


def create_app(sheets_client_factory=None):
    application = Flask(__name__)
    client_factory = sheets_client_factory or MatonSheetsClient

    @application.get("/healthz")
    def health():
        return jsonify(
            {
                "status": "ok",
                "service": "gogreen-meta-messaging-webhook",
                "configured": {
                    "meta_verify_token": bool(os.environ.get("META_VERIFY_TOKEN")),
                    "meta_app_secret": bool(os.environ.get("META_APP_SECRET")),
                    "sheets": bool(
                        os.environ.get("MATON_API_KEY") and os.environ.get("SHEET_ID")
                    ),
                },
            }
        )

    @application.get("/webhook/meta")
    def verify_webhook():
        mode = request.args.get("hub.mode") or request.args.get("hub_mode")
        supplied_token = request.args.get("hub.verify_token") or request.args.get(
            "hub_verify_token"
        )
        challenge = request.args.get("hub.challenge") or request.args.get("hub_challenge")
        expected_token = os.environ.get("META_VERIFY_TOKEN", "")
        if (
            mode == "subscribe"
            and expected_token
            and supplied_token
            and hmac.compare_digest(expected_token, supplied_token)
        ):
            return str(challenge or ""), 200, {"Content-Type": "text/plain"}
        return jsonify({"error": "verification_failed"}), 403

    @application.post("/webhook/meta")
    def receive_webhook():
        raw_body = request.get_data(cache=True)
        if not valid_signature(
            raw_body,
            request.headers.get("X-Hub-Signature-256", ""),
            os.environ.get("META_APP_SECRET", ""),
        ):
            return jsonify({"error": "invalid_signature"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid_json"}), 400
        records = normalize_payload(payload)
        if not records:
            return jsonify({"accepted": 0, "inserted": 0, "duplicates": 0}), 200
        try:
            result = client_factory().append_records(records)
        except Exception:
            LOGGER.exception("Failed to store Meta webhook records")
            return jsonify({"error": "storage_unavailable"}), 503
        return jsonify(
            {
                "accepted": len(records),
                "inserted": result["inserted"],
                "duplicates": result["duplicates"],
            }
        )

    return application


app = create_app()
