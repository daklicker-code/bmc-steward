#!/bin/sh
# Erzeugt das Quellarchiv einer Version reproduzierbar: gleicher Tag -> byte-gleiche Datei, egal wo und wann.
# Genutzt vom Workflow „Signieren“ und von tools/verify-release.sh.
#   tools/release-archive.sh v3.4.1 bmc-steward-v3.4.1.tar
set -eu
tag=${1:?Versions-Tag fehlt, z. B. v3.4.1}
out=${2:-bmc-steward-$tag.tar}
# Unkomprimiert: das tar-Format legt git selbst fest; gzip-Ausgaben können je nach Version abweichen.
git -c core.autocrlf=false archive --format=tar --prefix="bmc-steward-$tag/" "$tag^{commit}" > "$out"
echo "$out"
