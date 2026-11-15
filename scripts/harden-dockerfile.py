#!/usr/bin/env python3
"""Turn upstream backrest's Dockerfile.alpine into a self-contained hardened build.

Upstream's Dockerfile.alpine expects goreleaser to stage prebuilt binaries and
downloads restic at build time. We keep upstream's runtime stage (package list,
entrypoint, layout) and change it to:

  * build the web UI, backrest and docker-entrypoint from source with a current Go
    toolchain and bumped Go dependencies,
  * build restic (the version backrest requires) from source the same way, and
  * `apk upgrade` the runtime packages.

Every edit is anchored and must match exactly once; if upstream restructures the
file the script fails instead of producing an unhardened image.

Usage: harden-dockerfile.py <Dockerfile.alpine> <output Dockerfile>
"""
import re
import sys

GO_IMAGE = "golang:1.27-alpine"
NODE_IMAGE = "node:24-alpine"
PNPM_VERSION = "9"

BUILD_STAGES = f"""\
# ---------------------------------------------------------------------------
# backrest-hardened build stages (prepended to upstream's Dockerfile.alpine)
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM {NODE_IMAGE} AS webui-build
ARG BACKREST_VERSION=dev
WORKDIR /src/webui
RUN npm install --global --ignore-scripts pnpm@{PNPM_VERSION}
COPY webui/package.json webui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY webui/ ./
RUN BACKREST_BUILD_VERSION="$BACKREST_VERSION" pnpm run build

FROM --platform=$BUILDPLATFORM {GO_IMAGE} AS backrest-build
ARG TARGETOS
ARG TARGETARCH
ARG TARGETVARIANT
ARG BACKREST_VERSION=dev
ARG BACKREST_COMMIT=unknown
ARG BACKREST_DATE=unknown
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /src
COPY . .
COPY --from=webui-build /src/webui/dist ./webui/dist
RUN set -eux; \\
    sh hardening/go-bump.sh hardening/go-bumps.txt; \\
    export GOOS="$TARGETOS" GOARCH="$TARGETARCH" GOARM="${{TARGETVARIANT#v}}"; \\
    go build -trimpath -tags tray \\
      -ldflags "-s -w -X main.version=${{BACKREST_VERSION#v}} -X main.commit=$BACKREST_COMMIT -X main.date=$BACKREST_DATE" \\
      -o /out/backrest ./cmd/backrest; \\
    go build -trimpath -ldflags "-s -w" -o /out/docker-entrypoint ./cmd/docker-entrypoint; \\
    go version -m /out/backrest | head -n 3

FROM --platform=$BUILDPLATFORM {GO_IMAGE} AS restic-build
ARG TARGETOS
ARG TARGETARCH
ARG TARGETVARIANT
ARG RESTIC_VERSION
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
COPY hardening/ /hardening/
RUN set -eux; \\
    apk add --no-cache git; \\
    git clone --depth 1 --branch "v$RESTIC_VERSION" https://github.com/restic/restic.git /src; \\
    cd /src; \\
    sh /hardening/go-bump.sh /hardening/go-bumps.txt; \\
    export GOOS="$TARGETOS" GOARCH="$TARGETARCH" GOARM="${{TARGETVARIANT#v}}"; \\
    go build -trimpath -tags selfupdate,disable_grpc_modules -ldflags "-s -w" -o /out/restic ./cmd/restic; \\
    go version -m /out/restic | head -n 3

# ---------------------------------------------------------------------------
# upstream runtime stage (Dockerfile.alpine), hardened
# ---------------------------------------------------------------------------
"""


def sub_once(text, pattern, repl, what, flags=re.M):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != 1:
        sys.exit(f"harden-dockerfile: anchor for '{what}' matched {n} times (expected 1); "
                 "upstream Dockerfile.alpine changed, update scripts/harden-dockerfile.py")
    return new


def main(src, dst):
    with open(src) as f:
        text = f.read()

    if len(re.findall(r'^FROM ', text, re.M)) != 1:
        sys.exit("harden-dockerfile: expected a single-stage upstream Dockerfile.alpine")

    # Runtime packages: upgrade everything first.
    text = sub_once(text, r'^RUN apk (--no-cache add )',
                    r'RUN apk upgrade --no-cache && \\\n    apk \1', "apk add")

    # Binaries from our build stages instead of goreleaser's staging dir.
    text = sub_once(text, r'^COPY \$TARGETPLATFORM/backrest /backrest$',
                    'COPY --from=backrest-build /out/backrest /backrest', "copy backrest")
    text = sub_once(text, r'^COPY \$TARGETPLATFORM/docker-entrypoint /docker-entrypoint$',
                    'COPY --from=backrest-build /out/docker-entrypoint /docker-entrypoint',
                    "copy docker-entrypoint")

    # restic: our source build instead of downloading the release binary.
    text = sub_once(text, r'^RUN /backrest --install-deps-only\n',
                    '', "install-deps-only", flags=re.M)
    text = sub_once(text,
                    r'^RUN mkdir -p /bin && mv /root/\.local/share/backrest/restic /bin/restic$',
                    'COPY --from=restic-build /out/restic /bin/restic', "restic install")

    # Drop comments that described the removed steps.
    text = re.sub(r'^# (Binaries are staged per-platform by goreleaser.*|Run the binary to install dependencies.*|Setup path for restic)\n',
                  '', text, flags=re.M)

    # Labels identifying the hardened build.
    text = sub_once(text, r'^(ENTRYPOINT )',
                    'ARG HARDENED_VERSION=unknown\nARG HARDENED_SOURCE=unknown\nARG BACKREST_VERSION=dev\n'
                    'LABEL org.opencontainers.image.version=$HARDENED_VERSION \\\n'
                    '    org.backrest-hardened.source=$HARDENED_SOURCE \\\n'
                    '    org.backrest-hardened.upstream-version=$BACKREST_VERSION\n\n'
                    r'\1', "labels")

    with open(dst, "w") as f:
        f.write(BUILD_STAGES + text)
    print(f"harden-dockerfile: wrote {dst} ({GO_IMAGE}, {NODE_IMAGE})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
