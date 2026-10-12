# Python SDK release readiness

The project name is `giggy`, version `0.1.0`. On 2026-10-12 the public PyPI JSON endpoint returned 404, so no public release was verified. A 404 does not establish ownership or guarantee the name is available. Confirm the owner-controlled PyPI project and namespace before any publication.

## Completed source work

- Source-install onboarding and a first-speech example that fetches an account voice UUID.
- Offline request mapping, fresh/explicit idempotency, no streaming idempotency, mode/speed validation, response closure and HTTP error tests.
- Redirect rejection to prevent forwarding keys or replaying synthesis; explicit SDK User-Agent; bounded error bodies.
- Wheel includes the license and `py.typed`; build/check and installed-wheel checks are part of CI.
- Publishing is manually dispatched, with an exact confirmation phrase and a protected `pypi` environment. Ordinary pushes and tags do not publish.

## Owner approval steps

1. Review/merge the release-readiness PR, confirm public API/version and tested distributions.
2. Confirm `giggy` ownership. Configure PyPI Trusted Publishing for `giggy-ai/giggy-python`, workflow `publish.yml`, environment `pypi`. No API token goes into Git.
3. Configure the GitHub `pypi` environment to require an owner reviewer and prevent self-review. This task does not change organization/repository settings.
4. Decide whether a TestPyPI rehearsal and one explicitly authorized live API smoke are required. No billable smoke was performed by this implementation pass.
5. Explicitly approve publication, then dispatch the workflow from `main` with the exact current version and `publish giggy <version>` confirmation phrase. Review the environment approval and artifacts before the upload job.
6. After successful upload, verify fresh installation/import and package contents from PyPI. Update README installation instructions only after confirming publication.

CI uses fake keys and loopback/mocked HTTP. It checks buildability and behavior, not production inference quality. Remaining external gates are project ownership, Trusted Publisher configuration, environment reviewer configuration and explicit owner publication approval.
