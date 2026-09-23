"""Label documents against the MTL/1 rules, verified by the AWR/2 reference verifier."""

from __future__ import annotations

import pytest
from awr import SigningKey, verify_document

from histor.labels import CONTINUITY, OBSERVATION, LabelIssuer
from histor.subject import build_subject

TS = "2026-09-20T10:00:00Z"


@pytest.fixture
def issuer():
    return LabelIssuer(SigningKey.generate())


def subject(tools=None):
    return build_subject(server_name="io.example/s", registry="urn:awr:mtl:1:registry:test",
                         tools=tools or [{"name": "t", "description": "d"}], transport="streamable-http",
                         endpoint="https://s.example/mcp")


def test_observation_is_valid_and_carries_the_profile_members(issuer):
    doc = issuer.observation(subject(), observed_at=TS, issued_at=TS)
    result = verify_document(doc)
    assert result["valid"] and result["profile"] is None  # SPEC 10.4: a verdict is not a receipt
    assert "https://verify.modelmarket.dev/ns/awr/mtl/v1" in doc["@context"]
    body = doc["credentialSubject"]["mcpTrustLabel"]
    assert body["profile"] == "MTL/1" and body["reproducibility"] == "deterministic"
    assert doc["credentialSubject"]["verifiedWork"]["id"].startswith("urn:awr:mtl:1:subject:sha256:")


def test_three_methods_cannot_issue_fail(issuer):
    with pytest.raises(ValueError, match="MTL-METH-002"):
        issuer._issue(subject_ref=subject().reference(), verdict="fail", method=OBSERVATION, detail={},
                      evidence=[], issued_at=TS)


def test_continuity_fail_is_a_statement_about_two_digests(issuer):
    a = subject([{"name": "t", "description": "one"}])
    b = subject([{"name": "t", "description": "two"}])
    prior = issuer.observation(a, observed_at=TS, issued_at=TS)
    doc = issuer.continuity(current=b, prior_label=prior, prior_subject_ref=a.reference(),
                            prior_tool_digest=a.tool_set_digest, unchanged_since=None,
                            observed_at="2026-09-21T10:00:00Z", issued_at="2026-09-21T10:00:00Z")
    assert verify_document(doc)["valid"]
    assert doc["credentialSubject"]["method"]["id"] == CONTINUITY
    assert doc["credentialSubject"]["verdict"] == "fail"
    body = doc["credentialSubject"]["mcpTrustLabel"]
    assert body["priorLabel"]["id"] == prior["id"] and "unchangedSince" not in body
    assert "not an accusation" in body["scope"]


def test_continuity_against_a_failed_observation_is_inconclusive(issuer):
    a = subject()
    prior = issuer.observation(a, observed_at=TS, issued_at=TS)
    doc = issuer.continuity(current=None, prior_label=prior, prior_subject_ref=a.reference(), prior_tool_digest=None,
                            unchanged_since=None, observed_at=TS, issued_at=TS,
                            failure={"status": "timeout", "detail": ""})
    assert doc["credentialSubject"]["verdict"] == "inconclusive"
    assert doc["credentialSubject"]["mcpTrustLabel"]["observation"]["status"] == "timeout"


def test_the_vocabulary_never_claims_safety(issuer):
    """MTL/1 section 9.2: none of these words may render an outcome — so none belong in our scope text."""
    banned = ("safe", "secure", "audited", "certified", "approved", "trusted")
    docs = [issuer.observation(subject(), observed_at=TS, issued_at=TS)]
    docs.append(issuer.pattern_scan(subject(), observed_at=TS, issued_at=TS, pattern_set_id="p",
                                    pattern_set_digest=subject().digest, matches=[]))
    for doc in docs:
        scope = doc["credentialSubject"]["mcpTrustLabel"]["scope"].lower()
        assert not any(word in scope.split() for word in banned)
