#!/bin/bash
# PNLUG Rescue Script — restores a Rescuezilla backup (restoreimg/) from a Ventoy
# drive onto a target disk, then grows the Linux system partition to fill it.
#
# Design notes (see /home/luca/.claude/plans/synchronous-spinning-tiger.md, Phase 1):
#  - Linux-only cloning: the "grow" step identifies the real Linux root partition
#    by content, not by size/position guessing.
#  - Every destructive/critical step is checked; nothing claims success it didn't earn.
#  - The Ventoy drive itself can never be offered as a restore destination.

set -u

REQUIRED_CMDS=(lsblk blkid parted partprobe blockdev sfdisk rescuezilla
               e2fsck resize2fs)
# btrfs/xfs-progs are checked for individually at grow time (only needed if
# the restored root happens to use that filesystem) — not required upfront.

mountPath="/mnt/ventoy"
restoreImgPath=""
dataPartPath=""
ventoyDiskName=""   # e.g. "sda" — excluded from the destination disk list
scanMountPath="/mnt/pnlug_scan"

log()  { echo "$*"; }
err()  { echo "ERROR: $*" >&2; }

# ---------------------------------------------------------------------------
# Cleanup: unmount anything we mounted. Registered via trap, never calls exit
# itself, so the script's real exit code is preserved.
# ---------------------------------------------------------------------------
cleanup() {
    if mountpoint -q "$scanMountPath" 2>/dev/null; then
        umount "$scanMountPath" 2>/dev/null
    fi
    if [ -n "$mountPath" ] && mountpoint -q "$mountPath" 2>/dev/null; then
        umount "$mountPath" 2>/dev/null
        log "Unmounted $mountPath"
    fi
}
trap cleanup EXIT
# INT/TERM need their own explicit exit — a bare `trap cleanup INT` runs
# cleanup() but then bash *resumes* the script (trapping a signal overrides
# its default terminating action), so Ctrl+C would silently unmount things
# and keep going instead of stopping. cleanup() is idempotent (checks
# mountpoint first), so running it again via the EXIT trap below is harmless.
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# ---------------------------------------------------------------------------
# Fail fast if a required tool is missing, instead of discovering it mid-wipe.
# ---------------------------------------------------------------------------
check_dependencies() {
    local missing=()
    for cmd in "${REQUIRED_CMDS[@]}"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        err "Missing required tool(s): ${missing[*]}"
        err "This should never happen on the PNLUG Rescuezilla image — refusing to continue."
        exit 1
    fi
}

wait_for_device() {
    local device_path="$1" max_wait=30 wait_count=0
    log "Waiting for device $device_path to appear..."
    while [ "$wait_count" -lt "$max_wait" ]; do
        test -b "$device_path" && { log "Device $device_path found!"; return 0; }
        sleep 1
        wait_count=$((wait_count + 1))
    done
    err "Timeout waiting for device $device_path"
    return 1
}

# ---------------------------------------------------------------------------
# Find the Ventoy data partition by what it *contains*, not by guessing device
# names — but note: on a real live-desktop boot, Ventoy's own runtime claims
# the raw data partition and only leaves it mountable through a device-mapper
# node of the *same* base name (confirmed empirically: /dev/sdb1 comes back
# "already mounted or mount point busy" while /dev/mapper/sdb1 mounts fine
# and shows the real contents). So: scan real partitions for known markers
# (top-level restoreimg/, or /ventoy/ventoy.json), but always mount through
# the mapper node when one exists for that partition.
# ---------------------------------------------------------------------------
looks_like_ventoy_partition() {
    local mnt="$1"
    [ -d "$mnt/restoreimg" ] || [ -f "$mnt/ventoy/ventoy.json" ]
}

resolve_mountable() {
    local partition="$1" mapper="/dev/mapper/$(basename "$partition")"
    if [ -e "$mapper" ]; then
        echo "$mapper"
    else
        echo "$partition"
    fi
}

# Retries a few times on a transient mount failure: confirmed in testing
# that right at desktop/autostart time, the desktop's own automount daemon
# (udisks2/GVFS probing a freshly-inserted USB stick) can briefly hold the
# device, making a single mount attempt fail with "already mounted or busy"
# even though the very same mount succeeds moments later.
find_ventoy_partition() {
    local candidate target attempt
    mkdir -p "$scanMountPath"
    shopt -s nullglob
    for attempt in 1 2 3; do
        for candidate in /dev/sd*[0-9] /dev/nvme*p[0-9]* /dev/mmcblk*p[0-9]*; do
            test -b "$candidate" || continue
            target=$(resolve_mountable "$candidate")
            # Skip partitions already mounted elsewhere (e.g. the running live session).
            if findmnt -n "$target" >/dev/null 2>&1; then
                continue
            fi
            if mount -o ro "$target" "$scanMountPath" 2>/dev/null; then
                if looks_like_ventoy_partition "$scanMountPath"; then
                    umount "$scanMountPath"
                    dataPartPath="$candidate"
                    shopt -u nullglob
                    return 0
                fi
                umount "$scanMountPath"
            fi
        done
        [ "$attempt" -lt 3 ] && sleep 1
    done
    shopt -u nullglob
    return 1
}

# ---------------------------------------------------------------------------
# Read the minimum destination size (bytes) implied by the backup's own saved
# partition table dump, e.g. restoreimg/vda-pt.sf (an `sfdisk -d` dump has
# last-lba + sector-size). Echoes the byte count, or nothing if it can't tell
# (older/unexpected backup format — caller treats that as "can't verify, warn").
# ---------------------------------------------------------------------------
get_source_min_bytes() {
    local diskname sf lastlba sectorsize
    diskname=$(cat "$restoreImgPath/disk" 2>/dev/null) || return 1
    sf="$restoreImgPath/${diskname}-pt.sf"
    [ -f "$sf" ] || return 1
    lastlba=$(awk -F': *' '/^last-lba:/{print $2}' "$sf")
    sectorsize=$(awk -F': *' '/^sector-size:/{print $2}' "$sf")
    [ -n "$lastlba" ] && [ -n "$sectorsize" ] || return 1
    echo $(( (lastlba + 1) * sectorsize ))
}

# ---------------------------------------------------------------------------
# Identify which restored partition is the actual Linux system partition, by
# content (not size/position guessing): it's whichever partition has a root
# filesystem signature. Echoes the partition device path, or nothing.
# ---------------------------------------------------------------------------
find_system_partition() {
    local disk="$1" part mnt="$scanMountPath"
    mkdir -p "$mnt"
    for part in $(lsblk -lnpo NAME,TYPE "$disk" | awk '$2=="part"{print $1}'); do
        if mount -o ro "$(resolve_mountable "$part")" "$mnt" 2>/dev/null; then
            if [ -f "$mnt/etc/fstab" ] && { [ -d "$mnt/usr/bin" ] || [ -d "$mnt/bin" ]; }; then
                umount "$mnt"
                echo "$part"
                return 0
            fi
            umount "$mnt"
        fi
    done
    return 1
}

# True if $1 (a partition) is the last one on $2 (its disk) — i.e. there's no
# free space it could safely grow into unless it's physically at the end.
# Uses `sfdisk -d` (same start=/size= format the backups themselves are saved
# in) rather than an lsblk column, for units we know are consistent.
is_last_partition() {
    local part="$1" disk="$2" part_end=-1 max_end=-1 line dev start size end
    while read -r line; do
        [[ "$line" =~ ^(/dev/[a-zA-Z0-9]+).*start=[[:space:]]*([0-9]+),[[:space:]]*size=[[:space:]]*([0-9]+) ]] || continue
        dev="${BASH_REMATCH[1]}"; start="${BASH_REMATCH[2]}"; size="${BASH_REMATCH[3]}"
        end=$((start + size))
        [ "$dev" = "$part" ] && part_end=$end
        [ "$end" -gt "$max_end" ] && max_end=$end
    done < <(sfdisk -d "$disk" 2>/dev/null)
    [ "$part_end" -ge 0 ] && [ "$part_end" -eq "$max_end" ]
}

grow_system_partition() {
    local disk="$1" sys_part partition_num fs_type resize_success=1

    sys_part=$(find_system_partition "$disk")
    if [ -z "$sys_part" ]; then
        log "Could not identify a Linux system partition on $disk — skipping auto-grow."
        log "(Restore itself succeeded; you can grow partitions manually with gparted.)"
        return 0
    fi
    log "Identified Linux system partition: $sys_part"

    if ! is_last_partition "$sys_part" "$disk"; then
        log "$sys_part is not the last partition on $disk (something else follows it)."
        log "Skipping auto-grow — restore succeeded, but the extra space isn't safely reachable."
        return 0
    fi

    partition_num="${sys_part#"$disk"}"
    partition_num="${partition_num#p}"

    log "Growing partition $partition_num ($sys_part) to use all available space..."
    if ! parted -s -- "$disk" resizepart "$partition_num" 100%; then
        err "Failed to resize partition $sys_part."
        return 1
    fi
    partprobe "$disk"
    sleep 2

    # blkid probes the on-disk superblock directly; lsblk's FSTYPE column has
    # proven unreliable in this environment (same class of gap as its PARTN
    # column — see the grow-partition bug this whole function replaces).
    fs_type=$(blkid -o value -s TYPE "$sys_part")
    log "Resizing filesystem on $sys_part (detected: $fs_type)..."
    case "$fs_type" in
        ext2|ext3|ext4)
            e2fsck -f -p "$sys_part"
            resize2fs "$sys_part"
            resize_success=$?
            ;;
        btrfs)
            if command -v btrfs >/dev/null 2>&1; then
                local tmp="/mnt/pnlug_grow_tmp"
                mkdir -p "$tmp"
                if mount "$sys_part" "$tmp"; then
                    btrfs filesystem resize max "$tmp"
                    resize_success=$?
                    umount "$tmp"
                else
                    resize_success=1
                fi
                rmdir "$tmp"
            else
                err "btrfs-progs not installed — partition grown, filesystem not resized."
            fi
            ;;
        xfs)
            if command -v xfs_growfs >/dev/null 2>&1; then
                local tmp="/mnt/pnlug_grow_tmp"
                mkdir -p "$tmp"
                if mount "$sys_part" "$tmp"; then
                    xfs_growfs "$tmp"
                    resize_success=$?
                    umount "$tmp"
                else
                    resize_success=1
                fi
                rmdir "$tmp"
            else
                err "xfsprogs not installed — partition grown, filesystem not resized."
            fi
            ;;
        *)
            err "Unsupported filesystem type '$fs_type' — partition grown, filesystem not resized."
            resize_success=1
            ;;
    esac

    if [ "$resize_success" -eq 0 ]; then
        log "Filesystem resized successfully."
        return 0
    else
        err "Partition was resized but filesystem resize failed or was skipped."
        return 1
    fi
}

# ===========================================================================
# Main
# ===========================================================================
check_dependencies

log "PNLUG Rescue Script"

log "Checking for running rescuezilla processes..."
for process_name in rescuezilla rescuezillapy; do
    if pgrep -f "$process_name" >/dev/null; then
        log "Found running $process_name processes. Terminating..."
        pkill -f "$process_name"
        sleep 2
        pgrep -f "$process_name" >/dev/null && pkill -9 -f "$process_name"
    fi
done

# --- argument parsing (unchanged interface: -p/--ventoypath) ---------------
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--ventoypath) dataPartPath="$2"; shift 2 ;;
        -*|--*) echo "Unknown option $1"; echo "Usage: $0 [-p|--ventoypath <path>]"; exit 1 ;;
        *) POSITIONAL_ARGS+=("$1"); shift ;;
    esac
done
set -- "${POSITIONAL_ARGS[@]}"

if [ -z "$dataPartPath" ]; then
    if ! find_ventoy_partition; then
        err "Ventoy data partition not found automatically on any device."
        while [ -z "$dataPartPath" ] || ! test -b "$dataPartPath"; do
            read -rp "Specify the Ventoy data partition (e.g. /dev/sdb1), or press Enter to exit: " dataPartPath
            [ -z "$dataPartPath" ] && { log "Exiting."; exit 0; }
            test -b "$dataPartPath" || err "That path doesn't exist or isn't a block device."
        done
    fi
fi
log "Ventoy data partition: $dataPartPath"

# A partition with device-mapper holders (real Ventoy sticks have one — see
# resolve_mountable) makes `lsblk -no PKNAME` print one row per holder, not
# just one; only the first row is the actual parent disk. head -1 avoids
# ventoyDiskName silently becoming a multi-line string that never matches.
ventoyDiskName=$(lsblk -no PKNAME "$dataPartPath" 2>/dev/null | head -1)

# --- mount the Ventoy partition ---------------------------------------------
mkdir -p "$mountPath"
if mountpoint -q "$mountPath"; then
    umount "$mountPath" || { err "Failed to unmount $mountPath (in use?)."; exit 1; }
fi
if ! mount "$(resolve_mountable "$dataPartPath")" "$mountPath"; then
    err "Failed to mount $dataPartPath to $mountPath"
    exit 1
fi

restoreImgPath="$mountPath/restoreimg"
if ! test -d "$restoreImgPath"; then
    err "Can't find a restoreimg directory at $restoreImgPath."
    while ! test -d "$restoreImgPath"; do
        ls "$mountPath"
        read -rp "Path to the restoreimg directory (relative to $mountPath), or Enter to exit: " reply
        [ -z "$reply" ] && { log "Exiting."; exit 0; }
        restoreImgPath="$mountPath/$reply"
        test -d "$restoreImgPath" || err "Not a directory: $restoreImgPath"
    done
fi
log "Restoreimg directory found at $restoreImgPath."

# --- destination disk selection, excluding the Ventoy drive itself ---------
disks=()
shopt -s nullglob
for d in /dev/sd[a-z] /dev/nvme[0-9]*n[0-9]* /dev/mmcblk[0-9]*; do
    test -b "$d" || continue
    name="${d#/dev/}"
    [ "$name" = "$ventoyDiskName" ] && continue
    disks+=("$d")
done
shopt -u nullglob

if [ "${#disks[@]}" -eq 0 ]; then
    err "No candidate destination disks found (or only the Ventoy drive itself is present)."
    exit 1
fi

echo "Select destination disk (the Ventoy drive itself is hidden for safety):"
select disk in "${disks[@]}"; do
    if [ -n "$disk" ]; then
        log "You selected $disk"
        break
    else
        err "Invalid selection. Please try again."
    fi
done

# Belt and suspenders: never allow the Ventoy disk through, even if something
# above got confused.
if [ "${disk#/dev/}" = "$ventoyDiskName" ]; then
    err "Refusing to use the Ventoy drive itself as the restore destination."
    exit 1
fi

# --- size safety check -------------------------------------------------------
min_bytes=$(get_source_min_bytes)
dest_bytes=$(blockdev --getsize64 "$disk" 2>/dev/null)
if [ -n "$min_bytes" ] && [ -n "$dest_bytes" ]; then
    if [ "$dest_bytes" -lt "$min_bytes" ]; then
        err "$disk is smaller than the backed-up disk ($((dest_bytes/1024/1024))MiB < $((min_bytes/1024/1024))MiB)."
        err "Refusing to restore — this would fail partway through or corrupt the disk."
        exit 1
    fi
else
    log "Warning: couldn't verify destination size against the backup (older backup format?). Proceeding on your confirmation."
fi

echo "WARNING: This will completely wipe all partitions on $disk!"
echo "All data on $disk will be permanently lost."
read -rp "Are you sure you want to continue? Type 'YES' to confirm: " confirmation
[ "$confirmation" = "YES" ] || { log "Operation cancelled."; exit 0; }

# --- restore -----------------------------------------------------------------
# rescuezilla restore --overwrite-partition-table already rewrites the disk's
# partition table from the backup's own saved MBR/GPT bytes, so an extra
# sgdisk --zap-all here is redundant (and a GPT-only tool, which is wrong for
# an MBR-sourced backup) — dropped.
log "Restoring $restoreImgPath to $disk..."
# Stream to a log file as well as the terminal: confirmed in testing that
# stock Rescuezilla's own `rescuezilla` wrapper can return a nonzero exit
# code on a restore that actually succeeded (its cleanup path calls
# `systemctl --user --runtime unmask --quiet --` with no unit names, which
# systemctl rejects with "Too few arguments.", and that becomes the
# wrapper's own exit status). The restore engine's own "Successfully
# restored image partition" line is the trustworthy signal — trust it over
# a bare nonzero exit code.
restoreLog=$(mktemp)
rescuezilla restore --source "$restoreImgPath" --destination "$disk" --overwrite-partition-table 2>&1 | tee "$restoreLog"
restoreRc=${PIPESTATUS[0]}
if [ "$restoreRc" -ne 0 ] && ! grep -q "Successfully restored image partition" "$restoreLog"; then
    rm -f "$restoreLog"
    err "Restore failed. Not attempting to grow any partition."
    err "The disk may be left partially written — do not treat this as a working system."
    exit 1
fi
rm -f "$restoreLog"
log "Restore reported success."

partprobe "$disk"
sleep 2

if grow_system_partition "$disk"; then
    log "Rescue completed successfully — restore done, system partition grown to fill the disk."
else
    log "Rescue completed with warnings — restore succeeded, but the partition could not be auto-grown (see above)."
fi

log "You can now reboot your system."
read -rp "Reboot now? (y/n) " answer
[[ "$answer" =~ ^[Yy]$ ]] && { log "Rebooting..."; reboot; } || log "You can reboot later."
exit 0
