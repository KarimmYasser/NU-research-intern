#!/usr/bin/env bash
exec "$(cd "$(dirname "$0")" && pwd)/scripts/check_harness.sh" "$@"
