from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import brevo
from app import lead_notifications as ln
from app.brevo import BrevoLead
from app.lead_notifications import LeadNotification
from app.main import app


class _Resp:
    def raise_for_status(self):
        return None


def _settings(**overrides):
    base = {
        "lead_notify_webhook_url": "",
        "lead_notify_email_to": "",
        "resend_api_key": "",
        "lead_email_from": "",
        "lead_autoreply_enabled": False,
        "lead_autoreply_subject": "Auto subject",
        "brevo_api_key": "",
        "brevo_list_ids": "",
        "brevo_update_enabled": True,
        "brevo_company_attribute": "",
        "brevo_sector_attribute": "",
        "brevo_country_attribute": "",
        "brevo_lead_id_attribute": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_notify_new_lead_posts_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(ln, "settings", _settings(lead_notify_webhook_url="https://hooks.test/lead"))
    monkeypatch.setattr(ln.httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or _Resp())

    ln.notify_new_lead(LeadNotification(lead_id=7, name="Ana", phone="+34600000000", company="Pelu Ana"))

    assert len(calls) == 1
    assert calls[0][0][0] == "https://hooks.test/lead"
    assert calls[0][1]["json"]["lead"]["id"] == 7
    assert "Pelu Ana" in calls[0][1]["json"]["text"]


def test_notify_new_lead_sends_internal_and_autoreply_emails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ln,
        "settings",
        _settings(
            lead_notify_email_to="ventas@sprintiasolutions.com",
            resend_api_key="rk_test",
            lead_email_from="Sprintia <hola@sprintiasolutions.com>",
            lead_autoreply_enabled=True,
        ),
    )
    monkeypatch.setattr(ln.httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or _Resp())

    ln.notify_new_lead(
        LeadNotification(
            lead_id=8,
            name="Laura García",
            phone="+34611111111",
            email="laura@example.com",
            company="Bella",
        )
    )

    assert len(calls) == 2
    payloads = [c[1]["json"] for c in calls]
    assert payloads[0]["to"] == ["ventas@sprintiasolutions.com"]
    assert payloads[1]["to"] == ["laura@example.com"]
    assert "Laura" in payloads[1]["text"]


def test_create_lead_schedules_notification(monkeypatch):
    sent = []
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: sent.append(lead))

    client = TestClient(app)
    r = client.post(
        "/api/leads",
        data={
            "name": "Marta",
            "phone": "+34622222222",
            "email": "marta@example.com",
            "company": "Salon Marta",
            "country": "España",
            "consent": "on",
            "source": "hero",
        },
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert sent
    assert sent[0].email == "marta@example.com"
    assert sent[0].source == "hero"
    assert sent[0].country == "España"


def test_brevo_contact_payload():
    monkeypatch_settings = _settings(
        brevo_list_ids="12, 18",
        brevo_update_enabled=True,
        brevo_company_attribute="COMPANY",
        brevo_country_attribute="COUNTRY",
        brevo_lead_id_attribute="LEAD_ID",
    )
    original = brevo.settings
    brevo.settings = monkeypatch_settings
    try:
        payload = brevo._contact_payload(
            BrevoLead(
                lead_id=9,
                name="Marta López Ruiz",
                phone="+34 600 111 222",
                email="marta@example.com",
                company="Salon Marta",
                country="España",
            )
        )
    finally:
        brevo.settings = original

    assert payload["email"] == "marta@example.com"
    assert payload["listIds"] == [12, 18]
    assert payload["updateEnabled"] is True
    assert payload["attributes"]["SMS"] == "+34600111222"
    assert payload["attributes"]["FIRSTNAME"] == "Marta"
    assert payload["attributes"]["LASTNAME"] == "López Ruiz"
    assert payload["attributes"]["COMPANY"] == "Salon Marta"
    assert payload["attributes"]["COUNTRY"] == "España"
    assert payload["attributes"]["LEAD_ID"] == "9"


def test_brevo_sync_posts_contact(monkeypatch):
    calls = []
    monkeypatch.setattr(brevo, "settings", _settings(brevo_api_key="x-api", brevo_list_ids="4"))
    monkeypatch.setattr(brevo.httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or _Resp())

    brevo.sync_lead_contact(BrevoLead(lead_id=10, name="Ana", phone="+34600000000", email="ana@example.com"))

    assert len(calls) == 1
    assert calls[0][0][0] == "https://api.brevo.com/v3/contacts"
    assert calls[0][1]["headers"]["api-key"] == "x-api"
    assert calls[0][1]["json"]["listIds"] == [4]
