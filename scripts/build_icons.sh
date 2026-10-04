#!/usr/bin/env bash
# Regenerates the PWA icons in frontend/icons/ from docs/branding/spondbot-logo.png.
# Needs ImageMagick (`convert`). Run from anywhere: scripts/build_icons.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/docs/branding/spondbot-logo.png"
out="$root/frontend/icons"
mkdir -p "$out"

# Purpose "any": the sphere on a transparent background.
convert "$src" -trim +repage -resize 512x512 -background none -gravity center -extent 512x512 "$out/logo.png"
convert "$out/logo.png" -resize 192x192 "$out/icon-192.png"
cp "$out/logo.png" "$out/icon-512.png"
convert "$out/logo.png" -resize 32x32 "$out/favicon-32.png"

# Opaque tile for maskable icons (Android crops to a shape) and the iOS home screen
# (iOS fills transparency with black). The sphere sits inside the 80% safe zone.
tile() { # size, logo size, output
  convert -size "$1x$1" radial-gradient:'#7a3a9e-#2e1552' \
    \( "$out/logo.png" -resize "$2x$2" \) -gravity center -composite -depth 8 -strip "$3"
}
tile 512 360 "$out/icon-maskable-512.png"
tile 180 140 "$out/apple-touch-icon.png"

# Notification badge: white silhouette on transparency (Android tints it). A hex robot head.
convert -size 96x96 xc:none -fill white \
  -draw "polygon 48,10 78,26 84,56 66,84 30,84 12,56 18,26" \
  -fill none -stroke none \
  \( -size 96x96 xc:none -fill black \
     -draw "polygon 26,44 44,48 42,57 28,54" -draw "polygon 70,44 52,48 54,57 68,54" \) \
  -compose DstOut -composite -depth 8 PNG32:"$out/badge-96.png"
