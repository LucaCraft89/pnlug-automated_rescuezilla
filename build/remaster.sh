#!/bin/bash
# Runs INSIDE the pinned Ubuntu container (see build.sh) — never on the host.
#
#   1. Fetch the latest upstream Rescuezilla release ISO from GitHub
#   2. Extract it, unpack its squashfs
#   3. Drop in the PNLUG GUI/TUI/lib, branding, desktop entries, autostart
#   4. Repack the squashfs and rebuild the ISO (xorriso "replay" keeps the
#      original hybrid BIOS+UEFI/shim boot setup intact — same technique any
#      Ubuntu-derived-ISO respin uses, so we don't have to hand-roll isohybrid
#      offsets or El Torito flags ourselves)
#   5. Copy the finished ISO + the ready-to-copy ventoy/ folder to /pnlug/output
set -euo pipefail

CODENAME="${PNLUG_CODENAME:-noble}"
REPO=/pnlug/repo
SRC="$REPO/src"
OUT=/pnlug/output
WORK=/work
mkdir -p "$WORK" "$OUT"

echo "==> Installing build tools..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    xorriso squashfs-tools ca-certificates curl jq >/dev/null

echo "==> Looking up latest Rescuezilla release..."
RELEASE_JSON="$WORK/release.json"
curl -fsSL https://api.github.com/repos/rescuezilla/rescuezilla/releases/latest -o "$RELEASE_JSON"
TAG=$(jq -r '.tag_name' "$RELEASE_JSON")
ISO_URL=$(jq -r --arg codename "$CODENAME" \
    '.assets[] | select(.name | test("\\." + $codename + "\\.iso$")) | .browser_download_url' \
    "$RELEASE_JSON" | head -n1)
if [ -z "$ISO_URL" ] || [ "$ISO_URL" = "null" ]; then
    echo "No .$CODENAME.iso asset in release $TAG — available assets:" >&2
    jq -r '.assets[].name' "$RELEASE_JSON" >&2
    exit 1
fi
echo "==> Rescuezilla $TAG ($CODENAME): $ISO_URL"

ORIG_ISO="$WORK/orig.iso"
curl -fSL --progress-bar "$ISO_URL" -o "$ORIG_ISO"

echo "==> Extracting ISO..."
ISOTREE="$WORK/isotree"
rm -rf "$ISOTREE"
xorriso -indev "$ORIG_ISO" -osirrox on -extract / "$ISOTREE" >/dev/null

echo "==> Unpacking squashfs..."
SQUASHFS=$(find "$ISOTREE" -name "*.squashfs" | head -n1)
if [ -z "$SQUASHFS" ]; then
    echo "Couldn't find a .squashfs anywhere in the ISO — Rescuezilla changed its layout, needs a look." >&2
    exit 1
fi
echo "    found: ${SQUASHFS#$ISOTREE/}"
SQROOT="$WORK/squashfs-root"
rm -rf "$SQROOT"
# Rootless (unprivileged) containers can never create real device nodes —
# not even with --cap-add MKNOD, since mknod-ing an actual char/block device
# needs CAP_MKNOD in the *host* user namespace, which no in-container
# capability grants (confirmed: identical failure with the capability
# added). unsquashfs exits 2 for this ("some files not extracted") even
# though everything else extracts fine — confirmed by comparing file counts
# extracting the same image as real root vs. here: only the handful of
# /dev/* placeholders are missing, nothing else. That's fine: casper's own
# initramfs mounts a fresh devtmpfs over /dev at boot regardless of whatever
# static nodes shipped in the squashfs, so their absence here is a no-op.
set +e
unsquashfs -d "$SQROOT" "$SQUASHFS" >/dev/null
rc=$?
set -e
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    echo "unsquashfs failed with exit code $rc (not the known device-node-only case)." >&2
    exit "$rc"
fi

echo "==> Applying PNLUG customizations..."
install -m 0755 "$SRC/pnlug-rescue-gui"    "$SQROOT/usr/bin/pnlug-rescue-gui"
install -m 0755 "$SRC/pnlug-backup-gui"    "$SQROOT/usr/bin/pnlug-backup-gui"
install -m 0755 "$SRC/pnlug-rescue-tui"    "$SQROOT/usr/bin/pnlug-rescue-tui"
install -m 0755 "$SRC/pnlug-autostart.sh"  "$SQROOT/usr/bin/pnlug-autostart.sh"
install -m 0755 "$SRC/pnlugrescue.sh"      "$SQROOT/usr/bin/pnlugrescue.sh"
# The GUI/TUI scripts import these as plain sibling modules (sys.path is
# their own directory) — they need to live next to them in /usr/bin, not in
# a separate lib path.
install -m 0644 "$SRC/pnlug_rescue_lib.py" "$SQROOT/usr/bin/pnlug_rescue_lib.py"
install -m 0644 "$SRC/pnlug_gui_common.py" "$SQROOT/usr/bin/pnlug_gui_common.py"

install -Dm 0644 "$REPO/PNLug_marchio-circolare.png" "$SQROOT/usr/share/pixmaps/pnluglogo.png"

install -Dm 0644 "$SRC/desktop/pnlug-restore-gui.desktop" "$SQROOT/usr/share/applications/pnlug-restore-gui.desktop"
install -Dm 0644 "$SRC/desktop/pnlug-backup-gui.desktop"  "$SQROOT/usr/share/applications/pnlug-backup-gui.desktop"
install -Dm 0644 "$SRC/desktop/pnlug-rescue-tui.desktop"  "$SQROOT/usr/share/applications/pnlug-rescue-tui.desktop"
install -Dm 0644 "$SRC/desktop/pnlug-autostart.desktop"   "$SQROOT/etc/xdg/autostart/pnlug-autostart.desktop"

# The freedesktop autostart .desktop above only fires under a full session
# manager (xfce4-session, gnome-session, ...). Confirmed live: this
# release's actual live session is plain `openbox-session`, which never
# reads /etc/xdg/autostart at all — its own /etc/xdg/openbox/autostart
# shell script is the real entry point. Append to it directly so the GUI
# actually launches regardless of which one this particular release uses.
OPENBOX_AUTOSTART="$SQROOT/etc/xdg/openbox/autostart"
mkdir -p "$(dirname "$OPENBOX_AUTOSTART")"
touch "$OPENBOX_AUTOSTART"
if ! grep -q "pnlug-autostart.sh" "$OPENBOX_AUTOSTART" 2>/dev/null; then
    printf '\n# PNLUG Rescue (see /usr/bin/pnlug-autostart.sh)\n/usr/bin/pnlug-autostart.sh &\n' >> "$OPENBOX_AUTOSTART"
fi

# Stock Rescuezilla's own /home/ubuntu/.xprofile ends with `rescuezilla &`,
# auto-launching its own welcome-wizard GUI on every login — alongside ours.
# Confirmed live: with both open, clicking around the stock wizard can leave
# a stray rescuezillapy helper process running, which then makes our own
# restore/backup fail outright with "Only one rescuezillapy process is
# permitted." Strip that line so only the PNLUG-branded flow appears.
XPROFILE="$SQROOT/home/ubuntu/.xprofile"
if [ -f "$XPROFILE" ]; then
    sed -i '/^rescuezilla &$/d' "$XPROFILE"
    grep -q '^rescuezilla &$' "$XPROFILE" && { echo "stock rescuezilla autostart line survived the sed — .xprofile changed, needs a look." >&2; exit 1; }
fi

# Desktop shortcuts: every real user home already on the image, plus
# /etc/skel so any home created fresh at login gets them too.
for desktop_dir in "$SQROOT"/etc/skel "$SQROOT"/home/*; do
    [ -d "$desktop_dir" ] || continue
    mkdir -p "$desktop_dir/Desktop"
    for app in pnlug-restore-gui pnlug-backup-gui pnlug-rescue-tui; do
        cp "$SQROOT/usr/share/applications/$app.desktop" "$desktop_dir/Desktop/$app.desktop"
        chmod 0755 "$desktop_dir/Desktop/$app.desktop"
    done
done

echo "==> Enabling toram by default..."
# Rescuezilla's stock grub.cfg already ships a "Load USB into RAM" entry
# (boots with the casper `toram` kernel parameter) but defaults to the
# plain "Start Rescuezilla" entry instead. Make the RAM-loading one the
# default so the whole point of a Ventoy stick — the medium stays usable
# for restoreimg/ while the OS itself runs from RAM — happens without the
# user having to pick a non-default menu entry. `noeject` alongside `toram`
# stops casper from trying to eject/detach the boot medium once it's copied
# to RAM, which is exactly what would otherwise get in the way of us then
# mounting the Ventoy data partition to find restoreimg/.
GRUB_CFG="$ISOTREE/boot/grub/grub.cfg"
sed -i \
    -e 's/set default="en-item>standard-start-item"/set default="en-item>load-into-ram-item"/' \
    -e 's/\btoram\b \(fsck\.mode=skip\)/toram noeject \1/' \
    -e 's/^set timeout=10$/set timeout=1/' \
    "$GRUB_CFG"
grep -q 'default="en-item>load-into-ram-item"' "$GRUB_CFG" || { echo "grub.cfg default entry not found — Rescuezilla changed its menu, needs a look." >&2; exit 1; }
grep -q 'toram noeject' "$GRUB_CFG" || { echo "grub.cfg toram entry not found — Rescuezilla changed its menu, needs a look." >&2; exit 1; }
# The stock 10s timeout here is what makes the whole language->load-into-ram
# chain auto-drill on its own (confirmed live) — just slower than it needs to
# be for a stick with exactly one thing it's ever going to boot.
grep -q '^set timeout=1$' "$GRUB_CFG" || { echo "grub.cfg timeout=10 line not found — Rescuezilla changed its menu, needs a look." >&2; exit 1; }

echo "==> Repacking squashfs..."
rm -f "$SQUASHFS"
mksquashfs "$SQROOT" "$SQUASHFS" -comp xz -noappend >/dev/null
# Casper checks this against the unpacked tree size at boot time.
SIZE_FILE="$(dirname "$SQUASHFS")/filesystem.size"
if [ -f "$SIZE_FILE" ]; then
    du -sx --block-size=1 "$SQROOT" | cut -f1 > "$SIZE_FILE"
fi

echo "==> Rebuilding ISO (preserving the original hybrid BIOS+UEFI boot setup)..."
FINAL_ISO="$OUT/pnlug_zilla.iso"
rm -f "$FINAL_ISO"
# Only map the files we actually changed (squashfs + its size sidecar), not
# the whole extracted tree: -boot_image any replay reuses the *original*
# ISO's boot-critical data objects (El Torito images, GRUB2 MBR patch, etc.)
# by reference — re-adding the entire tree as fresh local files invalidates
# those references (confirmed: xorriso then refuses with "Cannot enable EL
# Torito boot image" / "Cannot refer by GRUB2 MBR to data outside of ISO
# 9660 filesystem"). Everything unchanged stays exactly as it was in
# $ORIG_ISO, which is what keeps the boot setup intact.
SQ_ISO_PATH="/${SQUASHFS#$ISOTREE/}"
MAP_ARGS=(-map "$SQUASHFS" "$SQ_ISO_PATH")
if [ -f "$SIZE_FILE" ]; then
    SIZE_ISO_PATH="/${SIZE_FILE#$ISOTREE/}"
    MAP_ARGS+=(-map "$SIZE_FILE" "$SIZE_ISO_PATH")
fi
# grub.cfg isn't referenced by the El Torito boot catalog itself (grub finds
# it at runtime via `search --set root --label` + `configfile`, not through
# a boot-catalog pointer), so mapping our edited copy in is safe here —
# unlike mapping the whole tree, which does break boot-catalog references.
MAP_ARGS+=(-map "$GRUB_CFG" "/${GRUB_CFG#$ISOTREE/}")
xorriso -indev "$ORIG_ISO" -outdev "$FINAL_ISO" \
    "${MAP_ARGS[@]}" \
    -boot_image any replay \
    -changes_pending yes \
    -compliance no_emul_toc >/dev/null

echo "==> Copying the ready-to-copy ventoy/ folder..."
rm -rf "$OUT/ventoy"
cp -r "$REPO/ventoy" "$OUT/ventoy"

echo "==> Done: $FINAL_ISO"
ls -lh "$FINAL_ISO"
