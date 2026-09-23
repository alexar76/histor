"""MTL/1 label documents — one per method, issued through the AWR/2 reference implementation.

Everything a label may and may not say is fixed by ``awr/adoption/mcp-trust-label/PROFILE.md``.
The parts that are easy to get wrong, restated where the code makes them:

* one method per label, and its verdict is that method's outcome alone (section 6.2);
* no ``score``, ever (section 6.4) — not even the gate's own 0..1;
* three of the four methods can never return ``fail``. Only continuity can, because it is a
  statement about two digests, not a judgement (section 7.4);
* ``scope`` is the honest sentence, inside the signature, so no page template can replace it.
"""

from __future__ import annotations

from typing import Any

from awr import SigningKey, canonical_sri, issue_verification_verdict

from histor.subject import Subject

MTL_CONTEXT = "https://verify.modelmarket.dev/ns/awr/mtl/v1"
LABEL_TYPE = "MCPTrustLabel"
ISSUER_NAME = "HISTOR (histor.modelmarket.dev)"

OBSERVATION = "urn:awr:mtl:1:method:tool-set-observation"
PATTERN_SCAN = "urn:awr:mtl:1:method:tool-def-pattern-scan"
CONTINUITY = "urn:awr:mtl:1:method:tool-set-continuity"
NAME_THREAT = "urn:awr:mtl:1:method:name-threat-match"

METHOD_NAMES = {
    OBSERVATION: "MTL/1 tool-set observation",
    PATTERN_SCAN: "MTL/1 tool-definition pattern scan (ARGUS WARDEN static-scan pattern set)",
    CONTINUITY: "MTL/1 tool-set continuity",
    NAME_THREAT: "MTL/1 name and coordinate threat match (ARGUS WARDEN built-in records)",
}
SHORT = {OBSERVATION: "observation", PATTERN_SCAN: "pattern-scan", CONTINUITY: "continuity", NAME_THREAT: "name-threat"}
ALLOWED_VERDICTS = {
    OBSERVATION: {"pass", "inconclusive"},
    PATTERN_SCAN: {"pass", "inconclusive"},
    CONTINUITY: {"pass", "fail", "inconclusive"},
    NAME_THREAT: {"pass", "inconclusive"},
}

SCOPE_OBSERVATION = (
    "At observedAt this server advertised exactly the {count} tool definitions whose MTL/1 canonical "
    "digest is toolSet.digestSRI, over the endpoint named in the subject descriptor. Nothing else is "
    "claimed: no source code was read, no package was inspected, and no tool was invoked."
)
SCOPE_OBSERVATION_INCONCLUSIVE = (
    "At observedAt this server answered tools/list, but the tool set could not be digested under "
    "MTL/1 for the reason given in reasons. The tool names are recorded; no digest is claimed."
)
SCOPE_SCAN = (
    "Pattern match over the tool names, descriptions and JSON schemas this server advertised at "
    "observedAt, using the pattern set named by patternSet. A match is a reason to read the definition, "
    "not a finding of fact. No source code was read, no package was inspected, and no tool was invoked."
)
SCOPE_CONTINUITY_PASS = (
    "The subject digest observed now equals the subject digest of priorLabel: the advertised tool "
    "definitions did not change between the two observations. This says nothing about the periods "
    "between observations, or about behaviour."
)
SCOPE_CONTINUITY_FAIL = (
    "The subject digest observed now differs from the subject digest of priorLabel: the advertised "
    "tool definitions changed between the two observations. A change is not an accusation; a version "
    "bump changes definitions too."
)
SCOPE_CONTINUITY_INCONCLUSIVE = (
    "No continuity comparison was possible at observedAt ({why}). Nothing is claimed about whether "
    "the advertised tool definitions changed."
)
SCOPE_NAME_THREAT = (
    "The server's registry name and endpoint were matched against the record set named by recordSet. "
    "Tool definitions were not examined by this method. A match is a naming-similarity signal, not "
    "evidence about code. The record set is not stated to have been current at observedAt."
)


class LabelIssuer:
    def __init__(self, key: SigningKey) -> None:
        self.key = key

    @property
    def did(self) -> str:
        return self.key.did

    def _issue(self, *, subject_ref: dict[str, str], verdict: str, method: str, detail: dict[str, Any],
               evidence: list[dict[str, Any]], issued_at: str) -> dict[str, Any]:
        if verdict not in ALLOWED_VERDICTS[method]:
            raise ValueError(f"verdict {verdict!r} is not permitted for {method} (MTL-METH-002)")
        body = {"profile": "MTL/1", "reproducibility": "deterministic", **detail}
        return issue_verification_verdict(
            {
                "verifiedWork": subject_ref,
                "verdict": verdict,
                "method": {"id": method, "name": METHOD_NAMES[method]},
                "evidence": evidence,
                "mcpTrustLabel": body,
            },
            self.key,
            valid_from=issued_at,
            created=issued_at,
            issuer_name=ISSUER_NAME,
            extra_types=[LABEL_TYPE],
            extra_context=[MTL_CONTEXT],
        )

    @staticmethod
    def _server(subject: Subject) -> dict[str, str]:
        server = subject.descriptor["server"]
        return {"name": server["name"], "registry": server["registry"]}

    @staticmethod
    def _tool_set(subject: Subject) -> dict[str, Any]:
        out: dict[str, Any] = {"count": subject.count}
        if subject.tool_set_digest:
            out["digestSRI"] = subject.tool_set_digest
        return out

    @staticmethod
    def _base_evidence(subject: Subject) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = [{"kind": "mtl-subject-descriptor", "digestSRI": subject.digest}]
        if subject.tool_set_digest:
            evidence.append({"kind": "mtl-tool-set", "digestSRI": subject.tool_set_digest})
        return evidence

    def observation(self, subject: Subject, *, observed_at: str, issued_at: str,
                    warden_pin: str | None = None) -> dict[str, Any]:
        if subject.hazard is None:
            verdict, scope, reasons = "pass", SCOPE_OBSERVATION.format(count=subject.count), []
        else:
            verdict, scope = "inconclusive", SCOPE_OBSERVATION_INCONCLUSIVE
            reasons = [{"code": subject.hazard.code, "detail": subject.hazard.detail}]
        detail: dict[str, Any] = {
            "observedAt": observed_at,
            "server": self._server(subject),
            "toolSet": self._tool_set(subject),
            "scope": scope,
        }
        if reasons:
            detail["reasons"] = reasons
        evidence = self._base_evidence(subject)
        if warden_pin:
            # Section 5.5 allows WARDEN's own hash as a cross-reference, never as the digest.
            evidence.append({"kind": "argus-tool-pin", "value": warden_pin})
        return self._issue(subject_ref=subject.reference(), verdict=verdict, method=OBSERVATION,
                           detail=detail, evidence=evidence, issued_at=issued_at)

    def undigestible(self, *, subject_ref: dict[str, str], server: dict[str, str], count: int, code: str,
                     message: str, observed_at: str, issued_at: str) -> dict[str, Any]:
        """Observation over a tool set that failed SUBJ-001/002/003 — no digestible descriptor exists."""
        return self._issue(
            subject_ref=subject_ref, verdict="inconclusive", method=OBSERVATION,
            detail={
                "observedAt": observed_at,
                "server": server,
                "toolSet": {"count": count},
                "scope": SCOPE_OBSERVATION_INCONCLUSIVE,
                "reasons": [{"code": code, "detail": message}],
            },
            evidence=[{"kind": "mtl-subject-descriptor", "digestSRI": subject_ref["digestSRI"]}],
            issued_at=issued_at,
        )

    def pattern_scan(self, subject: Subject, *, observed_at: str, issued_at: str, pattern_set_id: str,
                     pattern_set_digest: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
        entries = [
            {k: m[k] for k in ("code", "severity", "tier", "tool", "where")}
            for m in matches
        ]
        return self._issue(
            subject_ref=subject.reference(),
            verdict="pass" if not entries else "inconclusive",
            method=PATTERN_SCAN,
            detail={
                "observedAt": observed_at,
                "server": self._server(subject),
                "toolSet": self._tool_set(subject),
                "patternSet": {"id": pattern_set_id, "digestSRI": pattern_set_digest},
                "patternMatches": entries,
                "scope": SCOPE_SCAN,
            },
            evidence=self._base_evidence(subject) + [{"kind": "mtl-pattern-set", "digestSRI": pattern_set_digest}],
            issued_at=issued_at,
        )

    def name_threat(self, subject: Subject, *, observed_at: str, issued_at: str, record_set_id: str,
                    record_set_digest: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
        entries = [{k: m[k] for k in ("code", "severity", "pattern", "field")} for m in matches]
        return self._issue(
            subject_ref=subject.reference(),
            verdict="pass" if not entries else "inconclusive",
            method=NAME_THREAT,
            detail={
                "observedAt": observed_at,
                "server": self._server(subject),
                "toolSet": self._tool_set(subject),
                "recordSet": {"id": record_set_id, "digestSRI": record_set_digest},
                "recordMatches": entries,
                "scope": SCOPE_NAME_THREAT,
            },
            evidence=[{"kind": "mtl-subject-descriptor", "digestSRI": subject.digest},
                      {"kind": "mtl-record-set", "digestSRI": record_set_digest}],
            issued_at=issued_at,
        )

    def continuity(self, *, current: Subject | None, prior_label: dict[str, Any], prior_subject_ref: dict[str, str],
                   prior_tool_digest: str | None, unchanged_since: str | None, observed_at: str, issued_at: str,
                   failure: dict[str, str] | None = None, no_digest_code: str | None = None) -> dict[str, Any]:
        """Continuity against the latest label in this target's chain.

        *current* is None when this observation failed; the label then references the prior
        subject and says so, rather than inventing a descriptor for a server we could not read.
        """
        prior_digest = canonical_sri(prior_label)
        prior_ref = {
            "id": prior_label["id"],
            "digestSRI": prior_digest,
            "observedAt": prior_label["credentialSubject"]["mcpTrustLabel"]["observedAt"],
        }
        server = prior_label["credentialSubject"]["mcpTrustLabel"]["server"]
        detail: dict[str, Any] = {"observedAt": observed_at, "server": server, "priorLabel": prior_ref}
        if current is None:
            verdict = "inconclusive"
            detail["scope"] = SCOPE_CONTINUITY_INCONCLUSIVE.format(why="the current observation failed")
            detail["observation"] = failure or {"status": "error"}
            subject_ref = prior_subject_ref
            # The label points at the prior subject, so it restates that subject's tool set
            # (PROFILE 6.3: count, and the digest when the descriptor carried one).
            prior_tools = prior_label["credentialSubject"]["mcpTrustLabel"].get("toolSet") or {}
            detail["toolSet"] = {k: prior_tools[k] for k in ("count", "digestSRI") if k in prior_tools} or {"count": 0}
        else:
            subject_ref = current.reference()
            detail["toolSet"] = self._tool_set(current)
            if current.tool_set_digest is None or prior_tool_digest is None:
                code = no_digest_code or (current.hazard.code if current.hazard else "MTL-NUM-001")
                verdict = "inconclusive"
                detail["scope"] = SCOPE_CONTINUITY_INCONCLUSIVE.format(why=f"a side has no tool-set digest ({code})")
                detail["reasons"] = [{"code": code, "detail": "one of the two tool sets is not digestible"}]
            elif current.digest == prior_subject_ref["digestSRI"]:
                verdict = "pass"
                detail["scope"] = SCOPE_CONTINUITY_PASS
                detail["unchangedSince"] = unchanged_since or prior_ref["observedAt"]
            else:
                verdict = "fail"
                detail["scope"] = SCOPE_CONTINUITY_FAIL
        evidence = [
            {"kind": "mtl-subject-descriptor", "digestSRI": subject_ref["digestSRI"]},
            {"kind": "mtl-prior-label", "id": prior_label["id"], "digestSRI": prior_digest},
        ]
        return self._issue(subject_ref=subject_ref, verdict=verdict, method=CONTINUITY, detail=detail,
                           evidence=evidence, issued_at=issued_at)
