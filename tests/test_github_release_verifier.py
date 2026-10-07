import json
from pathlib import Path

import pytest


CONTRACT = str(Path("GitHubReleaseVerifier.py"))
API_URL = "https://api.github.com/repos/vercel/next.js/releases/tags/v15.0.0"


def release_payload(**overrides):
    payload = {
        "tag_name": "v15.0.0",
        "name": "Next.js 15.0.0",
        "draft": False,
        "prerelease": False,
        "published_at": "2024-10-21T15:00:00Z",
        "html_url": "https://github.com/vercel/next.js/releases/tag/v15.0.0",
    }
    payload.update(overrides)
    return payload


def mock_response(direct_vm, status, body):
    direct_vm.mock_web(r"^https://api\.github\.com/repos/vercel/next\.js/releases/tags/v15\.0\.0$", {
        "method": "GET",
        "status": status,
        "body": body,
    })


def test_known_public_release_is_canonical_and_stored(direct_vm, direct_deploy):
    mock_response(direct_vm, 200, json.dumps(release_payload()))
    contract = direct_deploy(CONTRACT)

    result = contract.verify_release("  Vercel/Next.js  ", " v15.0.0 ")

    assert result["status"] == "FOUND"
    assert result["repository"] == "vercel/next.js"
    assert result["requested_tag"] == "v15.0.0"
    assert result["canonical_tag"] == "v15.0.0"
    assert result["draft"] is False
    assert result["prerelease"] is False
    assert result["source_api_url"] == API_URL
    assert contract.get_last_verification() == result
    assert contract.get_verification_count() == 1


def test_nonexistent_tag_is_structured_not_found(direct_vm, direct_deploy):
    mock_response(direct_vm, 404, "{not required for 404}")
    contract = direct_deploy(CONTRACT)

    result = contract.verify_release("vercel/next.js", "v15.0.0")

    assert result["status"] == "NOT_FOUND"
    assert result["exists"] is False
    assert result["canonical_tag"] == ""
    assert contract.get_verification_count() == 1


@pytest.mark.parametrize("repository", ["", "vercel", "vercel/next.js/extra", "https://github.com/vercel/next.js", "vercel?x/next.js", "owner/../repo"])
def test_malformed_repository_is_rejected_before_web_access(direct_vm, direct_deploy, repository):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("repository"):
        contract.verify_release(repository, "v15.0.0")
    assert contract.get_verification_count() == 0


@pytest.mark.parametrize("tag", ["", "../v15", "v15?x=1", "v15#fragment", "v15%2Fother", "/v15", "v15/", "v15..0"])
def test_malformed_tag_is_rejected_before_web_access(direct_vm, direct_deploy, tag):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("tag"):
        contract.verify_release("vercel/next.js", tag)
    assert contract.get_verification_count() == 0


@pytest.mark.parametrize(
    ("status", "message"),
    [(403, "rate-limited or forbidden"), (429, "rate-limited or forbidden"), (500, "temporary failure"), (503, "temporary failure")],
)
def test_rate_limit_and_server_failures_are_not_not_found(direct_vm, direct_deploy, status, message):
    mock_response(direct_vm, status, "{}")
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert(message):
        contract.verify_release("vercel/next.js", "v15.0.0")
    assert contract.get_last_verification() == {}
    assert contract.get_verification_count() == 0


@pytest.mark.parametrize("body", ["not json", "[]", json.dumps({"tag_name": "v15.0.0"}), json.dumps(release_payload(draft=1))])
def test_malformed_api_json_fails_closed(direct_vm, direct_deploy, body):
    mock_response(direct_vm, 200, body)
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("github api returned malformed"):
        contract.verify_release("vercel/next.js", "v15.0.0")
    assert contract.get_verification_count() == 0


def test_validator_independently_fetches_and_rejects_disagreement(direct_vm, direct_deploy):
    mock_response(direct_vm, 200, json.dumps(release_payload()))
    contract = direct_deploy(CONTRACT)
    leader_result = contract.verify_release("vercel/next.js", "v15.0.0")

    # Swap the evidence visible to the validator after the leader has fetched.
    direct_vm.clear_mocks()
    mock_response(direct_vm, 200, json.dumps(release_payload(name="Different release title")))
    assert direct_vm.run_validator(leader_result=leader_result) is False


def test_storage_write_is_after_the_consensus_boundary(direct_vm, direct_deploy, monkeypatch):
    mock_response(direct_vm, 200, json.dumps(release_payload()))
    contract = direct_deploy(CONTRACT)

    import genlayer.gl.vm as gl_vm

    def reject_consensus(leader_fn, validator_fn):
        leader_value = leader_fn()
        assert validator_fn(gl_vm.Return(calldata={"status": "NOT_FOUND"})) is False
        raise RuntimeError("consensus rejected")

    monkeypatch.setattr(gl_vm, "run_nondet_unsafe", reject_consensus)
    with direct_vm.expect_revert("consensus rejected"):
        contract.verify_release("vercel/next.js", "v15.0.0")
    assert contract.get_last_verification() == {}
    assert contract.get_verification_count() == 0


def test_draft_and_prerelease_are_retained_as_stable_facts(direct_vm, direct_deploy):
    mock_response(direct_vm, 200, json.dumps(release_payload(draft=True, prerelease=True, published_at=None)))
    contract = direct_deploy(CONTRACT)

    result = contract.verify_release("VERCEL/next.js", "v15.0.0")

    assert result["draft"] is True
    assert result["prerelease"] is True
    assert result["published_at"] == ""


def test_tag_with_slash_is_percent_encoded_as_one_api_path_segment(direct_vm, direct_deploy):
    url = "https://api.github.com/repos/vercel/next.js/releases/tags/release%2Fv15"
    direct_vm.mock_web(r"^https://api\.github\.com/repos/vercel/next\.js/releases/tags/release%2Fv15$", {
        "method": "GET",
        "status": 404,
        "body": "{}",
    })
    contract = direct_deploy(CONTRACT)

    result = contract.verify_release("vercel/next.js", "release/v15")

    assert result["source_api_url"] == url
