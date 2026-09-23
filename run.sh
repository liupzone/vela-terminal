#!/usr/bin/env bash
# Run Vela straight from the source tree, without installing.
set -euo pipefail
exec /usr/bin/python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bin/vela" "$@"
