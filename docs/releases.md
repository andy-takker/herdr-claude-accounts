# Releases

Version is declared in `herdr-plugin.toml`. The initial version is `0.1.0`;
publication and creation of a GitHub release are explicit maintainer actions.
There is no npm or PyPI package and no automatic publishing workflow.

## Before publishing this repository

1. Review the source and Git history being pushed for personal data. Push only
   the intended public branch; do not publish private development notes or
   unrelated local branches with `--all` or `--mirror`.
2. Run `make check` and `herdr-app check herdr-app` on macOS. Commit the reviewed
   source, then run `make package`. This archives committed HEAD, not uncommitted
   working files. Extract the archive into a temporary directory and run
   `make test` there to verify the distributable itself.
3. Create the GitHub repository, set its default branch to `main`, and push the
   reviewed public branch. Enable private vulnerability reporting in repository
   settings. Wait for the actual hosted CI run; a local test pass is separate.
4. Update installation URLs in README after the repository is public. For the
   initial publication, source installation works without a release tag.

## Tagged release

1. Update the manifest version and CHANGELOG. Record actual tested host versions
   in `docs/compatibility.md` and run the manual scenarios listed there.
2. Commit the release changes, pass CI and tag that commit as `v0.1.0` (substitute
   the manifest version for later releases). Do not move a published tag.
3. Run `make package` at the tagged commit. Inspect the tarball: no `.git`, local
   notes, runtime profiles, SDK-generated files, credentials or cache directories.
4. Publish a GitHub release with the verified tarball, SHA-256 checksum and
   compatibility notes. The Makefile does not upload or publish anything.
5. Install from the extracted release into a permanent directory, configure the
   launcher and chosen Herdr integration, and verify the menu opens. Be explicit
   about which host installation modes were manually checked.

The optional native layout preview is a schematic, not a real-account screenshot.
Use synthetic profiles and redact private paths in future demos and recordings.
