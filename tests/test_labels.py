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


# -- MTL/1.2: the revision a label was issued under, and why a side has no digest ----------------
# agent-control-standard#38 (2026-11-06): "unknown" must keep apart a declaration nobody read from
# one that was read and declined by the profile, and "declined" is only true for a given revision.

def _undigestible():
    return subject([{"name": "p", "inputSchema": {"maximum": float("inf")}}])


def test_every_label_names_its_profile_revision(issuer):
    from histor.labels import PROFILE_REVISION
    a = subject()
    obs = issuer.observation(a, observed_at=TS, issued_at=TS)
    cont = issuer.continuity(current=a, prior_label=obs, prior_subject_ref=a.reference(),
                             prior_tool_digest=a.tool_set_digest, unchanged_since=None, observed_at=TS, issued_at=TS)
    for doc in (obs, cont):
        assert verify_document(doc)["valid"]
        assert doc["credentialSubject"]["mcpTrustLabel"]["profileRevision"] == PROFILE_REVISION == "1.2.0"
    assert "noDigest" not in obs["credentialSubject"]["mcpTrustLabel"]["toolSet"]
    assert "noDigest" not in cont["credentialSubject"]["mcpTrustLabel"]


def test_a_declined_set_says_not_digestible_with_the_code(issuer):
    doc = issuer.observation(_undigestible(), observed_at=TS, issued_at=TS)
    body = doc["credentialSubject"]["mcpTrustLabel"]
    assert verify_document(doc)["valid"] and doc["credentialSubject"]["verdict"] == "inconclusive"
    assert body["toolSet"]["noDigest"] == {"reason": "not_digestible", "code": "MTL-NUM-001"}
    assert body["reasons"][0]["code"] == "MTL-NUM-001"  # the 1.1 member stays


def test_an_undigestible_label_says_not_digestible(issuer):
    a = subject()
    doc = issuer.undigestible(subject_ref=a.reference(), server={"name": "io.example/s", "registry": "r"}, count=0,
                              code="MTL-SUBJ-001", message="nameless entry", observed_at=TS, issued_at=TS)
    assert doc["credentialSubject"]["mcpTrustLabel"]["toolSet"] == {
        "count": 0, "noDigest": {"reason": "not_digestible", "code": "MTL-SUBJ-001"}}


def test_a_failed_observation_is_not_observed_not_undigestible(issuer):
    a = subject()
    prior = issuer.observation(a, observed_at=TS, issued_at=TS)
    doc = issuer.continuity(current=None, prior_label=prior, prior_subject_ref=a.reference(), prior_tool_digest=None,
                            unchanged_since=None, observed_at=TS, issued_at=TS, failure={"status": "http-401"})
    assert doc["credentialSubject"]["mcpTrustLabel"]["noDigest"] == {"current": {"reason": "not_observed"}}


def test_continuity_names_each_side_without_a_digest_and_the_prior_revision(issuer):
    bad, good = _undigestible(), subject()
    prior = issuer.observation(bad, observed_at=TS, issued_at=TS)
    # A prior label from before 1.2 names no revision; the continuity label must not invent one.
    old = {**prior, "credentialSubject": {**prior["credentialSubject"], "mcpTrustLabel": {
        k: v for k, v in prior["credentialSubject"]["mcpTrustLabel"].items() if k != "profileRevision"}}}
    for p, revision in ((prior, "1.2.0"), (old, None)):
        doc = issuer.continuity(current=good, prior_label=p, prior_subject_ref=bad.reference(), prior_tool_digest=None,
                                unchanged_since=None, observed_at=TS, issued_at=TS, prior_no_digest_code="MTL-NUM-001")
        body = doc["credentialSubject"]["mcpTrustLabel"]
        expected = {"reason": "not_digestible", "code": "MTL-NUM-001"}
        if revision:
            expected["profileRevision"] = revision
        assert body["noDigest"] == {"prior": expected}
        assert doc["credentialSubject"]["verdict"] == "inconclusive"
    both = issuer.continuity(current=bad, prior_label=prior, prior_subject_ref=bad.reference(), prior_tool_digest=None,
                             unchanged_since=None, observed_at=TS, issued_at=TS, no_digest_code="MTL-NUM-001",
                             prior_no_digest_code="MTL-NUM-001")
    assert set(both["credentialSubject"]["mcpTrustLabel"]["noDigest"]) == {"current", "prior"}
