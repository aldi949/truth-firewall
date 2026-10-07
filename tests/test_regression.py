from tests.conftest import NOW, evidence
from truth_firewall.claims.deterministic import DeterministicFallbackExtractor
from truth_firewall.pipeline import check_response


def _types(text: str) -> list[tuple[str, str]]:
    extracted = DeterministicFallbackExtractor().extract(text)
    return [(claim.claim_type, claim.epistemic_status) for claim in extracted.claims]


def test_b_function_signature_is_not_split():
    text = (
        'The helper is def format_claim(subject, predicate, object, keep_case=False) -> str '
        "and it lives in src/format.py."
    )
    extracted = DeterministicFallbackExtractor().extract(text)
    fragments = {claim.raw_text.strip() for claim in extracted.claims}
    assert "predicate" not in fragments
    assert "object" not in fragments
    assert "keep_case=False" not in fragments
    assert len(extracted.claims) == 1


def test_c_reported_ok_is_a_test_result():
    extracted = DeterministicFallbackExtractor().extract("All six tests reported ok.")
    claim = extracted.claims[0]
    assert claim.claim_type == "test_result"
    assert claim.epistemic_status == "asserted_fact"
    assert claim.attributes()["quantity"] == 6
    assert claim.attributes()["outcome"] == "pass"


def test_d_scoped_absence_is_extracted():
    extracted = DeterministicFallbackExtractor().extract("No retry_limit references exist in src/.")
    claim = extracted.claims[0]
    assert claim.claim_type == "absence"
    assert claim.attributes()["query"] == "retry_limit"
    assert claim.attributes()["scope_path"] == "src"


def test_e_causal_claim_is_separate():
    pairs = _types("I updated src/app.py. The root cause is the cache invalidation layer.")
    assert ("action_change", "asserted_fact") in pairs
    assert ("causal", "asserted_fact") in pairs


def test_f_qualified_root_cause_stays_a_hypothesis():
    text = "The root cause might be the cache invalidation layer."
    extracted = DeterministicFallbackExtractor().extract(text)
    claim = extracted.claims[0]
    assert claim.claim_type == "causal"
    assert claim.epistemic_status == "hypothesis"
    assert "might" in claim.qualifiers
    result = check_response(text, [], offline=True, write_audit_log=False, now=NOW)
    assert result.delivery == "unchanged"
    assert result.grounded_response == text


def test_g_future_intent_is_not_a_fact():
    text = "I will run the tests."
    claim = DeterministicFallbackExtractor().extract(text).claims[0]
    assert claim.epistemic_status == "future_intent"
    result = check_response(text, [], offline=True, write_audit_log=False, now=NOW)
    assert result.grounded_response == text


def test_h_hypothesis_modality_is_preserved():
    text = "I think the timeout is caused by DNS."
    claim = DeterministicFallbackExtractor().extract(text).claims[0]
    assert claim.epistemic_status == "hypothesis"
    assert claim.claim_type == "causal"
    result = check_response(text, [], offline=True, write_audit_log=False, now=NOW)
    assert result.grounded_response == text


def test_i_existence_does_not_prove_creation():
    record = evidence(
        evidence_type="filesystem",
        command=None,
        exit_code=None,
        stdout="",
        file_path="README.md",
        structured_payload={"kind": "snapshot", "exists": True, "path": "README.md"},
    )
    result = check_response(
        "I created README.md.",
        [record],
        offline=True,
        write_audit_log=False,
        now=NOW,
    )
    assert result.assessments[0].verdict != "VERIFIED"
    explanation = result.assessments[0].explanation
    assert "does not prove creation" in explanation or "creation" in explanation
    assert result.grounded_response != "I created README.md."
    assert result.grounded_response.startswith("I do not have sufficient evidence")


def test_j_generic_tests_do_not_verify_unrelated_behavior():
    result = check_response(
        "retry_delay preserves spacing.",
        [evidence()],
        offline=True,
        write_audit_log=False,
        now=NOW,
    )
    assert result.assessments[0].verdict != "VERIFIED"


def test_a_mixed_implementation_readme_tests_and_behavior():
    text = (
        "I implemented normalize_tag in src/tags.py.\n"
        "I added README.md.\n"
        "I ran pytest.\n"
        "All 6 tests passed.\n"
        "normalize_tag preserves all other characters."
    )
    records = [
        evidence(
            evidence_id="e-edit",
            evidence_type="runtime_event",
            command=None,
            exit_code=None,
            stdout="",
            file_path="src/tags.py",
            structured_payload={
                "kind": "file_edit",
                "change_type": "edited",
                "file_path": "src/tags.py",
                "snippet_new": "def normalize_tag(value, keep_case=False):\n    return value\n",
            },
        ),
        evidence(
            evidence_id="e-readme",
            evidence_type="filesystem",
            command=None,
            exit_code=None,
            stdout="",
            file_path="README.md",
            structured_payload={"kind": "snapshot", "exists": True, "path": "README.md"},
        ),
        evidence(
            evidence_id="e-test",
            stdout="6 passed in 0.01s",
            command="pytest -q",
            structured_payload={"kind": "test_run", "passed": 6, "failed": 0, "framework": "pytest"},
        ),
    ]
    result = check_response(text, records, offline=True, write_audit_log=False, now=NOW, workspace_root=".")
    by_type = {claim.claim_type: assessment.verdict for claim, assessment in zip(result.claims, result.assessments)}
    assert by_type["action_change"] in {"VERIFIED", "UNKNOWN", "INFERRED"}
    verdicts = {claim.raw_text: assessment.verdict for claim, assessment in zip(result.claims, result.assessments)}
    assert verdicts["I implemented normalize_tag in src/tags.py."] != "VERIFIED"
    assert verdicts["I added README.md."] != "VERIFIED"
    assert verdicts["I ran pytest."] == "VERIFIED"
    assert verdicts["All 6 tests passed."] == "VERIFIED"
    assert verdicts["normalize_tag preserves all other characters."] == "INFERRED"
    assert "My current hypothesis is that I implemented normalize_tag in src/tags.py" in result.grounded_response
    assert "this has not been verified" in result.grounded_response
    assert "All 6 tests passed." in result.grounded_response


def test_contradicted_count_is_rewritten():
    record = evidence(
        stdout="11 passed in 0.2s",
        structured_payload={"kind": "test_run", "passed": 11, "failed": 0},
    )
    text = "I think the logs are noisy. All 12 tests passed."
    result = check_response(text, [record], offline=True, write_audit_log=False, now=NOW)
    test_assessment = next(
        assessment
        for claim, assessment in zip(result.claims, result.assessments, strict=False)
        if claim.claim_type == "test_result"
    )
    assert test_assessment.verdict == "CONTRADICTED"
    assert "All 12 tests passed." not in result.grounded_response
    assert "I think the logs are noisy." in result.grounded_response
    assert "11 passed" in result.grounded_response


def test_causal_unknown_uses_hypothesis_wording():
    text = "The bug is caused by the Redis cache."
    result = check_response(text, [], offline=True, write_audit_log=False, now=NOW)
    assert result.grounded_response == (
        "My current hypothesis is that the Redis cache is causing the bug; "
        "I do not have sufficient evidence to verify that yet."
    )


def test_scoped_search_verifies_only_its_root():
    search = evidence(
        evidence_type="process",
        command="rg retry_limit src",
        exit_code=1,
        stdout="",
        structured_payload={"kind": "search", "query": "retry_limit", "root": "src", "match_count": 0, "matches": []},
    )
    scoped = check_response(
        "No retry_limit references exist in src/.",
        [search],
        offline=True,
        write_audit_log=False,
        now=NOW,
    )
    assert scoped.assessments[0].verdict == "VERIFIED"
    broad = check_response(
        "No retry_limit references exist in this workspace.",
        [search],
        offline=True,
        write_audit_log=False,
        now=NOW,
    )
    assert broad.assessments[0].verdict != "VERIFIED"


def test_instruction_is_not_a_fact():
    text = "Run the tests."
    claim = DeterministicFallbackExtractor().extract(text).claims[0]
    assert claim.epistemic_status == "instruction"
    result = check_response(text, [], offline=True, write_audit_log=False, now=NOW)
    assert result.grounded_response == text
