#!/usr/bin/env bash
set -euo pipefail
version=1.2.707.0
archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT
curl --fail --silent --show-error --location \
  "https://s3.amazonaws.com/session-manager-downloads/plugin/$version/ubuntu_64bit/session-manager-plugin.deb" \
  --output "$archive"
printf '%s  %s\n' 91cc9a1a8df6f730622d5e11a8610be6e06d5858408e8029b56d0d63aedd0a68 "$archive" | sha256sum --check --status
sudo dpkg --install "$archive"
session-manager-plugin --version
