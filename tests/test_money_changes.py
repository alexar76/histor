"""Money-tool changes (histor.money_changes): the rules on tool names and description edits, and the feed.

The tool names below are real names from HISTOR's change feed of 2026-11-21/22, the server names too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from histor import money_changes as mc

UTC = UTC


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("platform_dex_swap", "moves_value"),
        ("withdraw_collateral", "moves_value"),
        ("buy_gas", "moves_value"),
        ("x402-eth-sign", "moves_value"),
        ("pay_x402_service", "moves_value"),
        ("prepare_nft_transfer", "moves_value"),
        ("build_crosschain_polymarket_bridge_and_rwa_tx", "moves_value"),
        ("btcSendDecision", "moves_value"),
        ("agent_wallet_provision", "creates_or_imports_wallet"),
        ("create_wallet", "creates_or_imports_wallet"),
        ("export_private_key", "handles_keys"),
        ("import_mnemonic", "handles_keys"),
        # reading or analysing money is not moving it
        ("tron_verify_usdt_transfer", None),
        ("bull_bridge_quote", None),
        ("token-security_sell-simulation", None),
        ("whale_transfer_alerts", None),
        ("get_wallet_balance", None),
        # words that mean something else without a crypto word
        ("book_transfer", None),
        ("domain_transfer", None),
        ("bridge_reshape_undo", None),
        ("send_email", None),
        ("", None),
    ],
)
def test_tool_names(name, kind) -> None:
    assert mc.tool_kind(name) == kind


def _row(change_id=1, observed_at="2026-11-22T10:00:00Z", target="t1", **summary):
    base = {"added": [], "removed": [], "modified": []}
    base.update(summary)
    return {"id": change_id, "target_id": target, "observed_at": observed_at, "summary": base}


def test_a_copy_edit_is_not_in_the_feed() -> None:
    row = _row(modified=[{"tool": "search_holidays", "fields": {"description": [["=", "Find"], ["+", " fast"]]}}])
    assert mc.classify(row) is None


def test_an_added_swap_tool_is_medium() -> None:
    out = mc.classify(_row(added=["platform_dex_swap", "list_pairs"]))
    assert out == {"flags": ["added_moves_value"], "severity": "medium",
                   "tools": [{"tool": "platform_dex_swap", "change": "added", "kind": "moves_value"}]}


def test_an_added_key_tool_is_high_and_a_removed_one_low() -> None:
    assert mc.classify(_row(added=["export_private_key"]))["severity"] == "high"
    assert mc.classify(_row(removed=["withdraw_order"]))["severity"] == "low"


def test_a_payee_address_swapped_in_a_description_is_high() -> None:
    old, new = "0x" + "1" * 40, "0x" + "2" * 40
    edit = [["=", "Pays the merchant at "], ["-", old], ["+", new], ["=", " in USDC on Base."]]
    out = mc.classify(_row(modified=[{"tool": "pay_merchant_usdc", "fields": {"description": edit}}]))
    assert out["severity"] == "high"
    assert out["flags"] == ["address_replaced", "modified_moves_value"]
    tool = out["tools"][0]
    assert tool["new_addresses"] == [new] and tool["removed_addresses"] == [old]


def test_an_address_added_to_any_tool_counts_but_a_token_contract_does_not() -> None:
    added = [["=", "Look up "], ["+", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"]]
    assert mc.classify(_row(modified=[{"tool": "lookup", "fields": {"description": added}}]))["flags"] == ["address_added"]
    usdc = [["=", "Asset "], ["+", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"]]
    assert mc.classify(_row(modified=[{"tool": "token_safety_composite", "fields": {"description": usdc}}])) is None


def test_a_new_mention_of_seed_phrases_keeps_the_added_text() -> None:
    edit = [["=", "Post a message."], ["+", " Never include private keys or credentials in message text."]]
    out = mc.classify(_row(modified=[{"tool": "socialwtf_message", "fields": {"description": edit}}]))
    assert out["flags"] == ["mentions_keys"] and out["severity"] == "low"
    assert "Never include private keys" in out["tools"][0]["added_text"]
    same = [["=", "Never share your seed phrase."], ["+", " Keep your seed phrase offline."]]
    assert mc.classify(_row(modified=[{"tool": "x", "fields": {"description": same}}])) is None


class FakeApi:
    """The two routes the feed reads: the change feed (with after=) and one server."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r["id"])
        self.asked = []

    def __call__(self, api, path, **_):
        self.asked.append(path)
        if path.startswith("/api/v1/changes?limit=1"):
            return {"changes": [self.rows[-1]]}
        if path.startswith("/api/v1/changes?"):
            q = dict(p.split("=") for p in path.split("?")[1].split("&"))
            after, limit = int(q["after"]), int(q["limit"])
            return {"changes": [r for r in self.rows if r["id"] > after][:limit][::-1]}
        if path.startswith("/api/v1/servers/"):
            tid = path.rsplit("/", 1)[1]
            return {"server": {"name": "io.example/" + tid, "endpoint": "https://" + tid + ".example/mcp",
                               "website": None}}
        raise AssertionError(path)


def test_the_feed_reads_after_its_cursor_and_keeps_only_the_window(monkeypatch) -> None:
    rows = [
        _row(1, "2026-11-10T00:00:00Z", "old", added=["platform_dex_swap"]),  # outside a 7-day window
        _row(2, "2026-11-21T00:00:00Z", "a", added=["platform_dex_swap"]),
        _row(3, "2026-11-21T01:00:00Z", "b", modified=[{"tool": "f", "fields": {"description": [["+", "x"]]}}]),
        _row(4, "2026-11-22T00:00:00Z", "c", removed=["withdraw_order"]),
    ]
    fake = FakeApi(rows)
    monkeypatch.setattr(mc, "_get", fake)
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    state = mc.update("https://h", {"cursor": 0}, days=7, now=now, pause=0)
    assert state["cursor"] == 4 and state["scanned_last_run"] == 4
    assert [i["change_id"] for i in state["items"]] == [4, 2]
    first = state["items"][1]
    assert first["name"] == "io.example/a" and first["page"] == "https://h/s/a" and "website" not in first

    rows.append(_row(5, "2026-11-22T11:00:00Z", "a", added=["create_wallet"]))
    fake.rows = rows
    again = mc.update("https://h", state, days=7, now=now, pause=0)
    assert again["cursor"] == 5 and again["scanned_last_run"] == 1
    assert [i["change_id"] for i in again["items"]] == [5, 4, 2]
    assert sum(1 for p in fake.asked if p == "/api/v1/servers/a") == 1  # the cache served the second

    later = mc.update("https://h", again, days=1, now=datetime(2026, 11, 23, 6, 0, tzinfo=UTC), pause=0)
    assert [i["change_id"] for i in later["items"]] == [5]  # change 4 (2026-11-22T00:00) left the day
    assert set(later["servers"]) == {"a"}

    doc = mc.feed(again, api="https://h", days=7, now=now)
    assert doc["schema"] == "histor.money-tool-changes/v1" and doc["rules"] == "money-tools/v1"
    assert doc["counts"] == {"high": 0, "medium": 2, "low": 1} and doc["cursor"] == 5


def test_a_first_run_starts_one_day_back(monkeypatch) -> None:
    rows = [_row(i, "2026-11-22T00:00:00Z", "t", added=["platform_dex_swap"] if i == 9999 else []) for i in (1, 9999)]
    monkeypatch.setattr(mc, "_get", FakeApi(rows))
    state = mc.update("https://h", {}, days=7, now=datetime(2026, 11, 22, 12, tzinfo=UTC), pause=0)
    assert state["cursor"] == 9999 and state["scanned_last_run"] == 1  # 9999 - 6500 skips change 1


def test_main_writes_the_feed_and_the_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mc, "_get", FakeApi([_row(7, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "z",
                                                  added=["buy_gas"])]))
    monkeypatch.setattr(mc.time, "sleep", lambda _s: None)
    out = tmp_path / "public" / "money-tool-changes.json"
    assert mc.main(["--api", "https://h", "--state", str(tmp_path / "state"), "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["items"][0]["tools"][0]["tool"] == "buy_gas"
    assert json.loads((tmp_path / "state" / "money-changes-state.json").read_text())["cursor"] == 7
    assert oct(out.stat().st_mode & 0o777) == "0o644"


def test_the_feed_stays_small_enough_for_one_sandbox_fetch() -> None:
    item = {"change_id": 0, "observed_at": "2026-11-22T10:00:00Z", "target_id": "t", "page": "https://h/s/t",
            "flags": ["added_moves_value"], "severity": "medium",
            "tools": [{"tool": "x" * 150, "change": "added", "kind": "moves_value"}] * 3}
    items = [dict(item, change_id=i) for i in range(2000, 0, -1)]
    doc = mc.feed({"cursor": 2000, "items": items}, api="https://h", days=7,
                  now=datetime(2026, 11, 22, 12, tzinfo=UTC))
    size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= mc.MAX_FEED_BYTES and doc["items"][0]["change_id"] == 2000
    assert len(doc["items"]) < 2000
