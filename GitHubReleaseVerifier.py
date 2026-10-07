# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""Verify public GitHub release facts through independently fetched API evidence.

This contract intentionally does not call an LLM.  Its only nondeterministic
input is the GitHub REST response obtained by the leader and, independently,
by every validator.
"""

import json
import re

from genlayer import *


GITHUB_API_ROOT = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"

MAX_REPOSITORY_LENGTH = 100
MAX_TAG_LENGTH = 128
MAX_RESPONSE_BYTES = 262_144
MAX_RELEASE_NAME_LENGTH = 512
MAX_URL_LENGTH = 512
MAX_TIMESTAMP_LENGTH = 64
MAX_STORED_RESULT_LENGTH = 2_048

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _normalize_repository(repository: str) -> str:
    """Return a canonical owner/repo identifier, or fail before URL creation."""
    if not isinstance(repository, str):
        raise ValueError("repository must be a string")

    normalized = repository.strip().lower()
    if len(normalized) < 3 or len(normalized) > MAX_REPOSITORY_LENGTH:
        raise ValueError("repository length is invalid")
    if normalized.count("/") != 1:
        raise ValueError("repository must be in owner/repo form")

    owner, name = normalized.split("/", 1)
    if not _OWNER_RE.fullmatch(owner) or not _REPOSITORY_RE.fullmatch(name):
        raise ValueError("repository contains unsupported characters")
    if name.endswith(".") or name.endswith(".git"):
        raise ValueError("repository name is invalid")
    return owner + "/" + name


def _normalize_tag(tag: str) -> str:
    """Normalize a conservative, URL-safe subset of Git tag names.

    Forward slashes are supported but are percent encoded into a single API
    path segment.  Characters which could create a query, fragment, or a new
    path component are rejected.
    """
    if not isinstance(tag, str):
        raise ValueError("tag must be a string")

    normalized = tag.strip()
    if len(normalized) == 0 or len(normalized) > MAX_TAG_LENGTH:
        raise ValueError("tag length is invalid")
    if not _TAG_RE.fullmatch(normalized):
        raise ValueError("tag contains unsupported characters")
    if (
        normalized.startswith("/")
        or normalized.endswith("/")
        or "//" in normalized
        or ".." in normalized
        or "@{" in normalized
        or normalized.endswith(".")
        or normalized.endswith(".lock")
    ):
        raise ValueError("tag is not a safe Git reference")
    return normalized


def _encode_path_segment(value: str) -> str:
    """Percent encode a validated ASCII value without relying on URL parsing."""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    encoded = ""
    for character in value:
        if character in safe:
            encoded += character
        else:
            encoded += "%" + format(ord(character), "02X")
    return encoded


def _release_api_url(repository: str, tag: str) -> str:
    owner, name = repository.split("/", 1)
    return (
        GITHUB_API_ROOT
        + "/repos/"
        + _encode_path_segment(owner)
        + "/"
        + _encode_path_segment(name)
        + "/releases/tags/"
        + _encode_path_segment(tag)
    )


def _require_text(value: object, field: str, maximum: int, *, nullable: bool = False) -> str:
    if value is None and nullable:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise gl.vm.UserError("github api returned malformed release payload")
    return value


def _require_bool(value: object) -> bool:
    # bool must be exact: Python treats integers as booleans in many contexts.
    if type(value) is not bool:
        raise gl.vm.UserError("github api returned malformed release payload")
    return value


def _canonical_not_found(repository: str, tag: str, api_url: str) -> dict:
    return {
        "repository": repository,
        "requested_tag": tag,
        "canonical_tag": "",
        "release_name": "",
        "exists": False,
        "draft": False,
        "prerelease": False,
        "published_at": "",
        "html_url": "",
        "source_api_url": api_url,
        "status": "NOT_FOUND",
    }


def _canonical_found(repository: str, tag: str, api_url: str, payload: object) -> dict:
    if not isinstance(payload, dict):
        raise gl.vm.UserError("github api returned malformed release payload")

    try:
        canonical_tag = _normalize_tag(
            _require_text(payload.get("tag_name"), "tag_name", MAX_TAG_LENGTH)
        )
    except ValueError:
        raise gl.vm.UserError("github api returned malformed release payload")
    # The endpoint is tag-specific.  A non-identical value is not acceptable
    # evidence for the requested tag, even if a proxy returned valid JSON.
    if canonical_tag != tag:
        raise gl.vm.UserError("github api release tag did not match requested tag")

    release_name = _require_text(
        payload.get("name"), "name", MAX_RELEASE_NAME_LENGTH, nullable=True
    )
    published_at = _require_text(
        payload.get("published_at"), "published_at", MAX_TIMESTAMP_LENGTH, nullable=True
    )
    html_url = _require_text(payload.get("html_url"), "html_url", MAX_URL_LENGTH)
    if not html_url.startswith("https://github.com/"):
        raise gl.vm.UserError("github api returned malformed release payload")

    return {
        "repository": repository,
        "requested_tag": tag,
        "canonical_tag": canonical_tag,
        "release_name": release_name,
        "exists": True,
        "draft": _require_bool(payload.get("draft")),
        "prerelease": _require_bool(payload.get("prerelease")),
        "published_at": published_at,
        "html_url": html_url,
        "source_api_url": api_url,
        "status": "FOUND",
    }


def _acquire_release_evidence(repository: str, tag: str) -> dict:
    """Fetch and normalize evidence.  Call only from a nondeterministic block."""
    api_url = _release_api_url(repository, tag)
    response = gl.nondet.web.request(
        api_url,
        method="GET",
        headers={
            "Accept": GITHUB_ACCEPT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "GenLayer-GitHubReleaseVerifier",
        },
    )

    if response.status == 404:
        return _canonical_not_found(repository, tag, api_url)
    if response.status == 403 or response.status == 429:
        raise gl.vm.UserError("github api rate-limited or forbidden (HTTP " + str(response.status) + ")")
    if response.status >= 500 and response.status <= 599:
        raise gl.vm.UserError("github api temporary failure (HTTP " + str(response.status) + ")")
    if response.status < 200 or response.status > 299:
        raise gl.vm.UserError("github api request failed (HTTP " + str(response.status) + ")")

    if response.body is None or len(response.body) > MAX_RESPONSE_BYTES:
        raise gl.vm.UserError("github api returned an invalid response body")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except Exception:
        raise gl.vm.UserError("github api returned malformed json")
    return _canonical_found(repository, tag, api_url, payload)


class GitHubReleaseVerifier(gl.Contract):
    """A bounded release verifier for public GitHub repositories."""

    last_verification_json: str
    verification_count: u64

    def __init__(self):
        self.last_verification_json = ""
        self.verification_count = u64(0)

    @gl.public.write
    def verify_release(self, repository: str, tag: str) -> dict:
        normalized_repository = _normalize_repository(repository)
        normalized_tag = _normalize_tag(tag)

        def leader_fn() -> dict:
            # Leader explicitly obtains public GitHub REST evidence.
            return _acquire_release_evidence(normalized_repository, normalized_tag)

        def validator_fn(leader_result: gl.vm.Result) -> bool:
            # Every validator obtains its own response from the same endpoint;
            # it is not merely judging the leader's claimed answer.
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                validator_evidence = _acquire_release_evidence(
                    normalized_repository, normalized_tag
                )
            except Exception:
                # A validator acquisition failure is a failed verification,
                # never evidence that the release is absent.
                return False
            return leader_result.calldata == validator_evidence

        # The VM returns only after the explicit validator comparison reaches
        # consensus.  Storage writes intentionally occur after this boundary.
        accepted_evidence = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        canonical_json = json.dumps(
            accepted_evidence, sort_keys=True, separators=(",", ":")
        )
        if len(canonical_json) > MAX_STORED_RESULT_LENGTH:
            raise ValueError("canonical verification result exceeds storage bound")

        self.last_verification_json = canonical_json
        self.verification_count = u64(int(self.verification_count) + 1)
        return accepted_evidence

    @gl.public.view
    def get_last_verification(self) -> dict:
        if self.last_verification_json == "":
            return {}
        return json.loads(self.last_verification_json)

    @gl.public.view
    def get_verification_count(self) -> int:
        return int(self.verification_count)
