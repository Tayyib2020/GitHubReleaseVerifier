# GitHubReleaseVerifier

`GitHubReleaseVerifier` is a GenLayer Intelligent Contract that verifies the
existence and stable metadata of a release in a public GitHub repository. It is
useful when an on-chain workflow needs an independently verifiable answer to a
simple release question, such as whether a particular version has been
published, whether it is a prerelease, or whether it is still a draft.

## No LLM required

This contract intentionally does **not** use an LLM. Its nondeterminism comes
from public external data: the leader and validators independently request the
GitHub REST API, normalize the stable release facts, and reach consensus on
those facts. This is a good fit for GenLayer because the network can adjudicate
live web evidence without trusting a single oracle or a leader's unsupported
claim.

## Evidence source

For a validated repository identifier and tag, the contract makes this request:

```text
GET https://api.github.com/repos/{owner}/{repo}/releases/tags/{percent-encoded-tag}
```

It uses GitHub's public REST API directly, with the documented GitHub media type
and API version headers. It does not scrape GitHub web pages and it requires no
API key or other secret.

## Consensus flow

```text
validated owner/repo + tag
            |
            v
leader independently fetches GitHub REST evidence
            |
            v
validators independently fetch the same endpoint
            |
            v
compare bounded, normalized stable facts exactly
            |
            v
GenLayer consensus -> persist accepted result
```

The validator does not merely inspect whether the leader answer "looks right".
It makes its own GitHub API request and accepts only an identical canonical
result. Storage changes occur only after the `run_nondet_unsafe` consensus call
returns.

The canonical result includes the normalized repository and requested tag,
canonical release tag, release name, `exists`, `draft`, `prerelease`,
`published_at`, release HTML URL, source API URL, and a `FOUND` or `NOT_FOUND`
status. It excludes request-local timestamps and other volatile transport data,
so validators compare stable release facts rather than incidental metadata.

## Inputs and public methods

The write method is:

```python
verify_release(repository: str, tag: str) -> dict
```

`repository` must be a public GitHub identifier in `owner/repo` form, for
example `vercel/next.js`. The tag is a bounded, conservative Git-reference
format; slash-containing tags are allowed and are percent-encoded as a single
API path segment. Inputs that could introduce a different host, path, query, or
fragment are rejected before a request is built.

Read methods:

- `get_last_verification() -> dict` returns the last accepted canonical result.
- `get_verification_count() -> int` returns the number of accepted
  verifications.

## Result and failure semantics

An HTTP `404` is definitive evidence that the requested release tag was not
found, and returns the structured `NOT_FOUND` result with `exists: false`.

An HTTP `403` or `429` can mean rate limiting or access restriction. `5xx`
responses, malformed JSON, unexpected JSON shapes/types, oversized bodies, and
other acquisition failures are treated as verification failures—not as proof
that the release is absent. This fail-closed behavior prevents a transient
GitHub or network fault from becoming a false `NOT_FOUND` record.

## Manual GenLayer Studio validation

The contract was manually validated in GenLayer Studio with **Normal (Full
Consensus)** using the exact source in this repository.

| Test | Outcome |
| --- | --- |
| `vercel/next.js`, `v15.0.0` | `FOUND`; `exists: true`; canonical tag `v15.0.0`; release name `v15.0.0`; not draft; not prerelease; `published_at: 2024-10-21T18:51:48Z`; verification count became `1`. |
| `vercel/next.js`, `v999.999.999` | `NOT_FOUND`; `exists: false`; verification count became `2`. |
| `https://github.com/vercel/next.js`, `v15.0.0` | Input rejected; verification count remained `2`. |

Studio-tested contract SHA-256:

```text
C7313E56748B719E2BA82D9B85DCA40DB389657F4961B950D7FA101B7643975F
```

## Deploy and test in GenLayer Studio

1. Open GenLayer Studio and create a contract from `GitHubReleaseVerifier.py`.
2. Deploy it with no constructor arguments.
3. Select **Normal (Full Consensus)** for write-method validation.
4. Call `verify_release("vercel/next.js", "v15.0.0")`.
5. Inspect the returned value, then use the two view methods to inspect the
   accepted state.

## Local development tests

Install the development dependencies in a virtual environment, then run:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The test suite uses mocked GitHub API responses and covers a known public
release, missing releases, malformed repository/tag input, 403/rate-limit,
5xx, malformed JSON, leader-validator disagreement, post-consensus storage,
draft/prerelease handling, and normalization. Current result: **28 passed**.

`tests/conftest.py` contains a narrow Windows compatibility shim for the
installed direct runner's stdin setup; it does not alter contract code or the
contract's runtime behavior.

## Security considerations

- Repository and tag values are length-bounded and normalized before URL
  construction.
- Only GitHub `owner/repo` identifiers are accepted; arbitrary URLs are never
  accepted as input.
- Tag path segments are percent-encoded after conservative Git-reference
  validation.
- Responses are size-bounded, decoded as UTF-8 JSON, and checked for expected
  object shape and exact field types.
- The contract stores only one compact canonical result plus a counter, keeping
  state bounded.

## Limitations

This verifier depends on the availability and public behavior of GitHub's API.
Unauthenticated GitHub API rate limits, temporary GitHub outages, and validator
network connectivity can prevent a verification from reaching consensus. Such
conditions fail closed and must be retried later; they are not evidence that a
release does not exist.
