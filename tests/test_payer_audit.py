"""Receipt labels must not invent buyers, discard revenue or turn missing evidence into negatives."""

from __future__ import annotations

import copy
import json

import pytest

from histor.__main__ import main as cli
from histor.payer_audit import ANNOTATIONS_FORMAT, CHAIN_FORMAT, TRANSFER, USDC, audit, markdown
from histor.payer_collect import Rpc, collect

PAYER = "0x" + "1" * 40
SELLER = "0x" + "2" * 40
TX = "0x" + "3" * 64
SOURCE = "https://example.org/published-research"


def tx(n):
    return "0x" + f"{n:064x}"


def sale(**changes):
    return {"payer": PAYER, "amountAtomic": "1000", "transaction": TX, "ownWallet": False,
            "at": "2026-10-25T00:00:00Z", "service": "ping", **changes}


def ledger(*sales):
    return {"seller": SELLER, "network": "eip155:8453", "generatedAt": "2026-10-25T00:01:00Z",
            "sales": list(sales) or [sale()]}


def event(*, payer=PAYER, recipient=SELLER, amount=1000, transaction=TX, index=0, block=85, **changes):
    return {"address": USDC, "topics": [TRANSFER, "0x" + payer[2:].zfill(64),
                                         "0x" + recipient[2:].zfill(64)],
            "data": "0x" + f"{amount:064x}", "transactionHash": transaction,
            "logIndex": hex(index), "blockNumber": hex(block), "removed": False, **changes}


def chain(logs=None, receipt_logs=None, *, complete=True):
    return {"schema": CHAIN_FORMAT, "chain_id": 8453, "token": USDC, "queried_payers": [PAYER],
            "window": {"from_block": 80, "to_block": 100, "from_at": "2026-10-24T00:00:00Z",
                       "to_at": "2026-10-25T00:01:00Z", "complete": complete},
            "logs": logs or [], "settlements": [{"transaction": TX, "receipt": {
                "transactionHash": TX, "blockNumber": "0x55", "status": "0x1",
                "from": "0x" + "4" * 40,  # Facilitator transaction sender is not the USDC payer.
                "logs": [event()] if receipt_logs is None else receipt_logs}}]}


def annotations(kind="known-probe", transactions=None):
    return {"schema": ANNOTATIONS_FORMAT, "items": [{"payer": PAYER, "kind": kind,
            "transactions": [TX] if transactions is None else transactions, "source": SOURCE,
            "note": "Operator identifies this exact research payment."}]}


def test_missing_chain_and_a_tiny_payment_do_not_prove_monitoring_or_no_demand():
    report = audit(ledger())
    payer = report["payers"][0]
    assert payer["label"] == "unknown" and payer["buyer_intent"] == "unknown"
    assert payer["chain_activity"]["transfers"] is None
    assert payer["settlements"][0]["status"] == "not-checked"
    assert report["summary"]["external_amount_usdc"] == "0.001000"
    assert report["summary"]["external_verified_amount_atomic"] == 0
    assert "was not collected" in markdown(report)


def test_known_probe_is_scoped_to_transactions_not_a_permanent_wallet_identity():
    report = audit(ledger(), annotations=annotations())
    assert report["payers"][0]["label"] == "known-probe"
    assert report["summary"]["external_amount_atomic"] == 1000
    report = audit(ledger(sale(), sale(transaction=tx(4), at="2026-10-26T00:00:00Z")),
                   annotations=annotations())
    assert report["payers"][0]["label"] == "unknown"
    assert report["summary"]["external_sales"] == 2


def test_repeated_purchases_alone_do_not_imply_a_monitor():
    d = ledger(sale(), sale(transaction=tx(4)))
    assert audit(d)["payers"][0]["label"] == "unknown"
    report = audit(d, annotations=annotations("example-input-match", [TX, tx(4)]))
    assert report["payers"][0]["label"] == "probe-like"
    assert report["payers"][0]["evidence"][0]["basis"] == "source-reported"


def test_broad_small_singleton_transfers_have_an_explicit_uncalibrated_basis():
    logs = [event(recipient="0x" + f"{n:040x}", transaction=tx(n), index=n) for n in range(10, 60)]
    report = audit(ledger(), chain=chain(logs))
    payer = report["payers"][0]
    assert payer["label"] == "probe-like" and payer["buyer_intent"] == "unknown"
    assert payer["chain_activity"]["recipients"] == 50
    assert report["policy"]["calibrated"] is False
    assert payer["evidence"][0]["code"] == "broad-small-singleton-transfers"
    # Amounts are retained, including for a wallet with this pattern.
    assert report["summary"]["external_amount_atomic"] == 1000


def test_large_amounts_or_one_frequent_recipient_do_not_match_the_sweeper_rule():
    large = [event(recipient="0x" + f"{n:040x}", amount=1_000_000, transaction=tx(n)) for n in range(10, 60)]
    assert audit(ledger(), chain=chain(large))["payers"][0]["label"] == "unknown"
    repeated = [event(transaction=tx(n)) for n in range(10, 80)]
    assert audit(ledger(), chain=chain(repeated))["payers"][0]["label"] == "unknown"


def test_descriptive_signals_remain_visible_below_the_strict_label_threshold():
    logs = [event(recipient="0x" + f"{n:040x}", amount=1000 if n < 50 else 100_000,
                  transaction=tx(n)) for n in range(10, 60)]
    payer = audit(ledger(), chain=chain(logs))["payers"][0]
    assert payer["label"] == "unknown"  # 80% small payments, below the explicit 90% rule.
    assert "broad-recipient-spread" in payer["pattern_signals"]
    assert "majority-transfers-at-most-0.01-USDC" in payer["pattern_signals"]
    assert payer["chain_activity"]["small_transfer_fraction"] == "0.800000"


def test_partial_activity_is_a_sample_not_full_window_coverage():
    report = audit(ledger(), chain=chain(complete=False))
    assert report["payers"][0]["chain_activity"]["complete"] is False
    assert report["payers"][0]["label"] == "unknown"
    other = "0x" + "5" * 40
    report = audit(ledger(sale(payer=other)), chain=chain())
    assert report["payers"][0]["chain_activity"]["available"] is False
    assert report["payers"][0]["chain_activity"]["recipients"] is None


def test_settlement_checks_the_usdc_event_not_the_facilitator_transaction_sender():
    report = audit(ledger(), chain=chain())
    assert report["payers"][0]["settlements_verified"] == 1
    assert report["summary"]["external_verified_amount_atomic"] == 1000
    for wrong in [event(amount=2000), event(recipient=PAYER), event(address="0x" + "9" * 40),
                  event(removed=True), event(transaction=tx(4))]:
        result = audit(ledger(), chain=chain(receipt_logs=[wrong]))
        assert result["payers"][0]["settlements_verified"] == 0
        assert result["summary"]["external_verified_amount_atomic"] == 0
        assert result["summary"]["external_amount_atomic"] == 1000


def test_one_transfer_cannot_verify_two_differently_timestamped_receipt_rows():
    report = audit(ledger(sale(), sale(at="2026-10-25T00:00:01Z")), chain=chain())
    assert report["payers"][0]["sales"] == 2
    assert report["payers"][0]["settlements_verified"] == 1


def test_exact_duplicates_are_disclosed_and_do_not_inflate_counts():
    log = event()
    report = audit(ledger(sale(), sale()), chain=chain([log, log]))
    assert report["summary"]["sales"] == 1
    assert report["summary"]["duplicate_receipts_ignored"] == 1
    assert report["payers"][0]["chain_activity"]["transfers"] == 1
    broken = event(amount=2)
    with pytest.raises(ValueError, match="conflicting chain events"):
        audit(ledger(), chain=chain([log, broken]))


def test_missing_ownership_is_not_counted_as_external_and_conflicts_are_rejected():
    missing = sale()
    del missing["ownWallet"]
    report = audit(ledger(missing))
    assert report["summary"]["externalPayers"] == 0
    assert report["payers"][0]["ownership"] == "unknown"
    assert report["warnings"]
    with pytest.raises(ValueError, match="conflicting ownWallet"):
        audit(ledger(sale(), sale(transaction=tx(4), ownWallet=True)))


def test_seller_claims_are_checked_against_rows():
    d = ledger()
    d["totals"] = {"sales": 20, "externalPayers": 50}
    report = audit(d)
    assert len(report["warnings"]) == 2
    assert report["summary"]["externalPayers"] == 1


@pytest.mark.parametrize("amount", [1.5, -1, True, "NaN", "1e3", "-100"])
def test_non_integer_or_negative_amounts_are_rejected(amount):
    with pytest.raises(ValueError, match="non-negative integer"):
        audit(ledger(sale(amountAtomic=amount)))


def test_wrong_chain_or_unscoped_annotations_are_rejected():
    d = ledger()
    d["network"] = "eip155:1"
    with pytest.raises(ValueError, match="Base ledger"):
        audit(d)
    c = chain()
    c["chain_id"] = 1
    with pytest.raises(ValueError, match="Base"):
        audit(ledger(), chain=c)
    with pytest.raises(ValueError, match="name transactions"):
        audit(ledger(), annotations=annotations(transactions=[]))
    c = chain([event(block=101)])
    with pytest.raises(ValueError, match="outside declared"):
        audit(ledger(), chain=c)


def test_markdown_does_not_render_annotation_markup():
    a = annotations()
    a["items"][0]["note"] = "<script>x</script> [link](javascript:x) `code`\n# heading"
    rendered = markdown(audit(ledger(), annotations=a))
    assert "<script>" not in rendered and "[link]" not in rendered


def test_cli_does_not_load_production_settings_or_modify_its_inputs(tmp_path, monkeypatch, capsys):
    source = tmp_path / "receipts.json"
    source.write_text(json.dumps(ledger()))
    before = source.read_bytes()
    monkeypatch.setenv("HISTOR_PROFILE", "prod")
    monkeypatch.setenv("HISTOR_DATABASE_URL", "not-a-valid-database")
    assert cli(["payer-audit", "--receipts", str(source), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["payers"][0]["label"] == "unknown"
    assert source.read_bytes() == before
    assert cli(["payer-audit", "--receipts", str(source), "-o", str(source)]) == 2
    assert "overwrite" in capsys.readouterr().err
    assert source.read_bytes() == before
    source.write_text("[]")
    assert cli(["payer-audit", "--receipts", str(source)]) == 2
    assert "JSON object" in capsys.readouterr().err


class FakeRpc:
    origin = "https://rpc.example.org"

    def __init__(self, *, fail=False, chain_id=8453):
        self.fail, self.chain_id, self.reads = fail, chain_id, []

    def call(self, method, params):
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(100)
        if method == "eth_getBlockByNumber":
            n = int(params[0], 16)
            return {"hash": tx(n), "timestamp": hex(1_792_886_400 + n * 2)}
        if method == "eth_getLogs":
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            if self.fail and lo == 86:
                raise ValueError("unavailable range")
            return [event()] if lo <= 85 <= hi else []
        if method == "eth_getTransactionReceipt":
            return chain()["settlements"][0]["receipt"]
        raise AssertionError(method)


def test_collector_keeps_window_and_receipt_checks_separate_and_marks_incomplete_ranges():
    result = collect(ledger(), FakeRpc(), days=8 / 86400, chunk_size=2)
    assert result["window"]["from_block"] == 84
    assert result["window"]["to_block"] == 88
    assert result["window"]["complete"] is True
    assert audit(ledger(), chain=result)["payers"][0]["settlements_verified"] == 1
    result = collect(ledger(), FakeRpc(fail=True), days=8 / 86400, chunk_size=2)
    assert result["window"]["complete"] is False
    assert result["window"]["failed_ranges"][0]["from_block"] == 86
    with pytest.raises(ValueError, match="not Base"):
        collect(ledger(), FakeRpc(chain_id=1))


def test_rpc_rejects_mutating_methods_before_network_access():
    rpc = Rpc("https://rpc.example.org")
    for method in ["eth_sendRawTransaction", "personal_sign", "eth_sendTransaction"]:
        with pytest.raises(ValueError, match="allowlist"):
            rpc.call(method, [])
    assert rpc.requests == 0


def test_collector_capture_budget_cannot_silently_drop_logs_or_claim_complete(monkeypatch):
    monkeypatch.setattr("histor.payer_collect.MAX_CAPTURE_BYTES", 1)
    result = collect(ledger(), FakeRpc(), days=8 / 86400, chunk_size=2)
    assert result["window"]["complete"] is False
    assert result["window"]["failed_ranges"]
    assert result["logs"] == []
    assert all("error" in row for row in result["settlements"])


def test_reducer_does_not_mutate_inputs_and_is_deterministic():
    d, c, a = ledger(), chain(), annotations()
    before = copy.deepcopy((d, c, a))
    first = audit(d, chain=c, annotations=a)
    assert first == audit(d, chain=c, annotations=a)
    assert (d, c, a) == before
