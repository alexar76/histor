"""An outside auditor catches a log that shrinks, forks, rewrites or swaps its key."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.audit import AuditFailure, audit
from histor.audit import main as audit_main
from tests.conftest import make_target, tool


@pytest.fixture
def logged(world):
    world["mcp"].tools["/a"] = [tool("a", "one")]
    world["targets"].append(make_target("io.example/a", "/a"))
    world["services"].crawler.run()
    with TestClient(create_app(world["services"]), base_url="http://log") as client:
        yield client, world


def test_a_first_run_checks_the_head_and_saves_it(logged, tmp_path):
    client, _ = logged
    state = tmp_path / "sth.json"
    report = audit("http://log", state=state, client=client)
    assert report["signature"] == "ok" and "consistency" not in report
    assert json.loads(state.read_text())["treeSize"] == report["treeSize"]


def test_growth_is_proven_consistent_and_a_label_is_proven_included(logged, tmp_path):
    client, world = logged
    state = tmp_path / "sth.json"
    audit("http://log", state=state, client=client)
    world["mcp"].tools["/a"] = [tool("a", "two")]
    world["clock"].advance(days=1)
    world["services"].crawler.run()
    label = world["services"].store.entries(0, 1)[0]["id"]
    report = audit("http://log", state=state, client=client, label_id=label)
    assert report["consistency"].startswith("ok (") and report["label"]["inclusion"] == "ok"
    assert audit("http://log", state=state, client=client)["consistency"] == "unchanged"


def test_a_rewritten_or_shrunk_or_foreign_history_fails(logged, tmp_path):
    client, _ = logged
    state = tmp_path / "sth.json"
    good = audit("http://log", state=state, client=client)
    kept = json.loads(state.read_text())
    state.write_text(json.dumps({**kept, "rootHash": "00" * 32}))
    with pytest.raises(AuditFailure, match="rewrote"):
        audit("http://log", state=state, client=client)
    state.write_text(json.dumps({**kept, "treeSize": good["treeSize"] + 5}))
    with pytest.raises(AuditFailure, match="shrank"):
        audit("http://log", state=state, client=client)
    state.write_text(json.dumps({**kept, "log": "did:key:z6MkOther"}))
    with pytest.raises(AuditFailure, match="key changed"):
        audit("http://log", state=state, client=client)
    state.write_text(json.dumps({**kept, "treeSize": 1, "rootHash": "11" * 32}))
    with pytest.raises(AuditFailure, match="NOT consistent"):
        audit("http://log", state=state, client=client)


def test_the_command_line_exit_status(logged, tmp_path, monkeypatch, capsys):
    client, _ = logged
    import histor.audit as mod

    monkeypatch.setattr(mod.httpx, "Client", lambda **kw: client)
    assert audit_main(["http://log", "--state", str(tmp_path / "s.json")]) == 0
    (tmp_path / "s.json").write_text(json.dumps({"log": "did:key:z6MkOther", "treeSize": 1, "rootHash": "00"}))
    assert audit_main(["http://log", "--state", str(tmp_path / "s.json")]) == 1
    assert "AUDIT FAILED" in capsys.readouterr().err


def test_an_audit_that_cannot_finish_is_incomplete_not_a_verdict(monkeypatch, capsys):
    """Exit 2 for "could not check", never 1: an empty log or a timeout is not a forged head."""
    import httpx
    import pytest

    from histor import audit as audit_module

    did = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"

    def serve(routes):
        def handle(request):
            status, body = routes.get(request.url.path, (404, {}))
            if status == "raise":
                raise httpx.ConnectTimeout("slow")
            return httpx.Response(status, json=body)
        return httpx.Client(base_url="https://log.test", transport=httpx.MockTransport(handle))

    empty = serve({"/api/v1/issuer": (200, {"did": did})})
    with pytest.raises(audit_module.AuditIncomplete, match="no signed tree head"):
        audit_module.audit("https://log.test", client=empty)

    down = serve({"/api/v1/issuer": ("raise", None)})
    with pytest.raises(audit_module.AuditIncomplete, match="ConnectTimeout"):
        audit_module.audit("https://log.test", client=down)

    monkeypatch.setattr(audit_module, "audit", lambda *a, **k: (_ for _ in ()).throw(audit_module.AuditIncomplete("x")))
    assert audit_module.main(["https://log.test"]) == 2
    assert "AUDIT INCOMPLETE" in capsys.readouterr().err
