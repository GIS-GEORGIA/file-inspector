#!/usr/bin/env bash
# Build the QGIS plugin ZIP (Plugins → Manage and Install → Install from ZIP).
set -euo pipefail
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version=//p' file_inspector/metadata.txt)
mkdir -p dist
out="dist/file_inspector-${version}.zip"
rm -f "$out"
zip -qr "$out" file_inspector -x '*__pycache__*' '*.pyc'
echo "$out"
