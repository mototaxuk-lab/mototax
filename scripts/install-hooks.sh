#!/usr/bin/env bash
#
# One-time setup: enable this repo's git hooks (.githooks/).
# Run once per clone:   ./scripts/install-hooks.sh
#
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true

echo "✓ Git hooks enabled (core.hooksPath=.githooks)."
echo "  pre-push will keep the README version block in sync with CHANGELOG.md."
