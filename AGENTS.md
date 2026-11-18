# Public NetBox Packer CI guidance

- Preserve the original lint, formatting, complete static tests, package metadata, NetBox source check, Docker smoke matrix, Page Coverage assertions and strict documentation build.
- Keep the public source check on its original Python 3.13, NetBox v4.6.1, PostgreSQL 16 and Redis 7 contract. Do not replace these services or application content with another provider's source history.
- `CI complete` and `E2E complete` must fail when any original producer fails, is cancelled or is skipped. Preserve main, maintenance and integration branch admission. Only obsolete runs for the same pull request can supersede each other.
- Screenshot capture is a read-only artifact handoff. Keep capture and strict build validation, but do not restore direct source pushes or unmerged gh-pages publication. Adopt reviewed screenshot artifacts through a normal pull request.
- The separate documentation publisher uses actual main source. Its temporary `CI_QUALIFICATION_ONLY` guard suppresses only publication during a separately admitted qualification. It does not waive the strict build or authorize a dispatch.
- Keep release publishers outside source-CI tasks. Run all tests, workflow lint and changed-function complexity checks before publishing a candidate. Read README.md for the complete screenshot review procedure.
