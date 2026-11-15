# backrest-hardened

Rebuilds upstream [backrest](https://github.com/garethgeorge/backrest) release tags from source, with current
toolchains and dependencies. Each build is gated on a Trivy scan and an end-to-end smoke test, and the result is
published to `ghcr.io/rmonk/backrest`.

No fork: each build clones upstream at the tag in `UPSTREAM_VERSION` and generates a hardened Dockerfile from
upstream's own `Dockerfile.alpine`.

## What gets changed

| Component | Upstream image | Hardened |
|---|---|---|
| `backrest`, `docker-entrypoint` | goreleaser build (go1.26.0 at v1.14.1) | built from the tag with current Go; modules in `overlay/go-bumps.txt` raised to latest |
| `restic` | release binary downloaded by `backrest --install-deps-only` | the exact version backrest requires (read from `RequiredResticVersion`), built from source with current Go and the same module bumps |
| Alpine packages | frozen at release time (`alpine:latest` on that day) | `apk upgrade`, rebuilt weekly |
| Web UI | built by goreleaser hook | built in a Node stage from `webui/` with pnpm 9 |

Upstream's package list, `rclone selfupdate`, entrypoint and layout are kept as they are, because the runtime stage is
upstream's file with anchored edits (`scripts/harden-dockerfile.py`). If upstream restructures `Dockerfile.alpine`,
the build fails instead of shipping something unhardened.

backrest uses the `restic` on `$PATH` when its version is at least `RequiredResticVersion`. Our build reports the
same version string (`restic 0.19.1 compiled with go1.27.x`), so backrest uses `/bin/restic` and never downloads one.
The smoke test checks this.

## Tags

- `latest-hardened`: newest successful build of the current `UPSTREAM_VERSION`
- `<upstream>-hardened`, e.g. `v1.14.1-hardened`
- `<upstream>-hardened-<YYYYMMDD>`: immutable, use this to pin or roll back

## Automation

- `build.yml`: runs on push/PR, manually, and weekly (Mon 05:47 UTC). It runs prepare, then build, then the Trivy
  gate (fixable CRITICAL/HIGH fail the build), then the smoke test, then pushes (on main only).
  The smoke test drives backrest's API: it sets an instance, adds a repo (restic init), adds a plan, runs a backup and
  lists snapshots. Then it runs `restic check --read-data` and a restore with diff, and checks that
  rclone/ssh/docker/curl run.
- `watch-upstream.yml`: runs daily. It opens a PR bumping `UPSTREAM_VERSION` when backrest releases, and dispatches
  the build on that branch.

## Known remaining findings

- `GO-2026-5932` (`golang.org/x/crypto/openpgp`, severity UNKNOWN, no fix: the package is deprecated). Used by
  backrest to verify restic release signatures and by restic's `self-update`. Neither path runs in this image,
  because restic is bundled and self-update is not used.

## Local build

```bash
scripts/prepare.sh
docker build -f build/src/Dockerfile.hardened $(sed 's/^/--build-arg /' build/build-args) \
  -t backrest:hardened build/src
scripts/smoke-test.sh backrest:hardened        # DOCKER=podman for podman
```
