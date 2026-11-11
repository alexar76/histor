from datetime import datetime

from fastapi.testclient import TestClient

from histor.app import create_app
from scripts.journey_report import summarize


def test_journey_accepts_only_empty_same_origin_named_events(world):
    services = world["services"]
    headers = {"Origin": services.settings.public_base, "Sec-Fetch-Site": "same-origin"}
    path = "/api/v1/journey/devto/check_succeeded"
    with TestClient(create_app(services)) as client:
        allowed = client.post(path, headers=headers)
        assert allowed.status_code == 204 and not allowed.content
        assert "access-control-allow-origin" not in allowed.headers
        assert client.post(path, headers={**headers, "Origin": "https://unrelated.example"}).status_code == 403
        assert client.post(path, headers={**headers, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post(path, headers={**headers, "Sec-GPC": "1"}).status_code == 404
        assert client.post(path + "?extra=value", headers=headers).status_code == 404
        assert client.post("/api/v1/journey/devto/raw_tools", headers=headers).status_code == 404
        assert client.post(path, headers=headers, json={"text": "do not collect"}).status_code == 413


def test_summary_separates_qa_bots_and_operator_traffic():
    def line(ip, source, event, *, ua="Mozilla/5.0", when="11/Nov/2026:12:00:00 +0000", status=204):
        return f'{ip} - - [{when}] "POST /api/v1/journey/{source}/{event} HTTP/2.0" {status} 0 "-" "{ua}"'

    events = [
        line("192.0.2.1", "x", "record_viewed"), line("192.0.2.1", "x", "sample_succeeded"),
        line("192.0.2.2", "qa", "sample_succeeded", ua="HeadlessChrome"),
        line("192.0.2.3", "devto", "check_succeeded"),
        line("192.0.2.4", "github", "check_started", ua="Mozilla/5.0 crawler"),
        line("192.0.2.1", "x", "pilot_contact_clicked", status=429),
        line("192.0.2.1", "x", "watch_viewed", when="12/Nov/2026:00:00:00 +0000"),
        line("192.0.2.1", "evil", "check_succeeded"),
    ]
    report = summarize(events, start=datetime.fromisoformat("2026-11-11T00:00:00Z"),
                       end=datetime.fromisoformat("2026-11-12T00:00:00Z"), own_ips={"192.0.2.3"})
    assert report["bySource"] == {"x": {"record_viewed": 1, "sample_succeeded": 1}}
    assert report["qaEvents"] == {"sample_succeeded": 1}
    assert report["excludedEvents"] == {"own": 1, "non_browser_or_known_bot": 1}
    assert report["externalBrowserEvents"]["watch_viewed"] == 0
