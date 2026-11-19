#!/bin/sh
# Prüft, dass eine Version unverändert aus diesem Repository stammt (Sigstore-Bescheinigung von GitHub).
# Braucht git und die GitHub-CLI (gh, angemeldet). Im ausgecheckten Repository ausführen:
#   tools/verify-release.sh v3.4.1
set -eu
tag=${1:?Versions-Tag fehlt, z. B. v3.4.1}
repo=${REPO:-daklicker-code/bmc-steward}
git fetch -q --tags origin
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
file="$tmp/bmc-steward-$tag.tar"
"$(dirname "$0")/release-archive.sh" "$tag" "$file" >/dev/null
echo "Archiv: $(sha256sum "$file" 2>/dev/null || shasum -a 256 "$file")"
gh attestation verify "$file" --repo "$repo" \
  --signer-workflow "$repo/.github/workflows/attest.yml"
echo "OK: Inhalt von $tag ist von GitHub Actions in $repo signiert."
