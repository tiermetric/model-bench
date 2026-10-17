#!/usr/bin/env bash
# Run from an exported --edition all kit. Prepare the image before going offline.
# Image tag at registry lookup: python:3.12-slim (2026-10-16).
set -euo pipefail
cd -- "$(dirname -- "$0")"
exec docker run --rm --pull never --network none --read-only \
  --user "$(id -u):$(id -g)" --pids-limit 256 --memory 2g \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD,dst=/kit,readonly" -w /kit \
  python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 \
  python3 -B rejudge_workflow.py
