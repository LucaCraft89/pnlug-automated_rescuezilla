#!/usr/bin/env python3
"""
Shared backend logic for the PNLUG Rescue GUI/TUI (restore + backup).

This is a Python port of the tested bash logic in pnlugrescue.sh (see
/home/luca/.claude/plans/synchronous-spinning-tiger.md, Phase 1) — same
safety rules, re-verified independently in Phase 2/3 testing:
  - The Ventoy drive itself is never offered as a restore/backup-source
    destination.
  - The Linux system partition is identified by content (fstab + usr/bin),
    not by size/position guessing.
  - Every external command's exit status is checked; nothing is reported
    as successful that didn't actually succeed.

Single source of truth for the GUI and TUI front-ends.
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

LogFn = Callable[[str], None]


def ensure_root():
    """Re-exec this process under sudo if it isn't already root. Confirmed
    live on real hardware — a bug this codebase never actually exercised
    before, since every prior test happened to relaunch these tools from an
    already-root terminal: none of the four ways a user can start these
    tools (autostart, or any of the three Desktop shortcuts) elevate
    privileges at all, so every real operation in this module (mount,
    parted, sfdisk, resize2fs, ...) was silently failing on a genuine
    unprivileged desktop login. It looked exactly like "can't find/mount
    the Ventoy drive" — every candidate partition fails to mount, instantly,
    every retry, forever — with nothing to say it was a permissions problem
    and not a missing/slow drive.

    `sudo -E` preserves DISPLAY/XAUTHORITY so a re-exec'd GTK window still
    shows up on the same X session; `-n` fails fast with a clear error
    instead of hanging on a password prompt with no TTY to answer it on, if
    the live user's sudo ever isn't passwordless."""
    if os.geteuid() == 0:
        return
    os.execvp("sudo", ["sudo", "-n", "-E", sys.executable] + sys.argv)


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def _noop_log(_line: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Disk / partition discovery
# ---------------------------------------------------------------------------

@dataclass
class Disk:
    path: str            # /dev/sda
    size_bytes: int
    model: str = ""
    removable: bool = False
    distro: str = ""     # e.g. "Alpine Linux v3.24" — see detect_distro_name

    @property
    def size_human(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if size < 1024 or unit == "TiB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TiB"

    @property
    def label(self) -> str:
        """What to show in a disk-picker row: the distro name if we could
        read one off the disk (more useful than a generic hardware model —
        tells the user *what's actually on it*), else the hardware model."""
        return self.distro or self.model or "unknown model"


def _candidate_disk_paths():
    paths = []
    paths += glob.glob("/dev/sd[a-z]")
    paths += [p for p in glob.glob("/dev/nvme*") if re.fullmatch(r"/dev/nvme\d+n\d+", p)]
    paths += [p for p in glob.glob("/dev/mmcblk*") if re.fullmatch(r"/dev/mmcblk\d+", p)]
    return sorted(paths)


def _disk_model(disk_path: str) -> str:
    name = os.path.basename(disk_path)
    try:
        with open(f"/sys/block/{name}/device/model") as f:
            return f.read().strip()
    except OSError:
        return ""


def _disk_removable(disk_path: str) -> bool:
    name = os.path.basename(disk_path)
    try:
        with open(f"/sys/block/{name}/removable") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def get_ventoy_disk_name(data_part_path: str) -> Optional[str]:
    """Parent disk kernel name (e.g. 'sda') of the Ventoy data partition.

    When the partition has device-mapper holders (real Ventoy sticks do —
    see resolve_mountable), `lsblk -no PKNAME` prints one row per holder,
    not just one: the partition's own row plus one per dm node sitting on
    top of it. Only the first row is the actual parent disk we want; the
    rest just repeat the *partition's* name and would otherwise turn this
    into a multi-line string that never equality-matches anything."""
    r = _run(["lsblk", "-no", "PKNAME", data_part_path])
    lines = r.stdout.strip().splitlines()
    return lines[0] if lines else None


def _unmount_retry(mnt: str, attempts: int = 3, delay: float = 0.5) -> None:
    """Best-effort unmount with a couple of retries, falling back to a lazy
    unmount. Confirmed live in testing: a plain `umount` can transiently
    fail (kernel still settling right after a partition table change/
    resize elsewhere on the same disk) — an unnoticed failure here used to
    leave the mountpoint alive for tempfile.TemporaryDirectory's own
    cleanup to trip over: shutil.rmtree hitting real files on a
    still-mounted, read-only filesystem ("Read-only file system:
    'mboot.c32'", "...: 'exec'" — different files, same root cause,
    whichever partition happened to be mounted there at the time)."""
    for _ in range(attempts):
        if _run(["umount", mnt]).returncode == 0:
            return
        time.sleep(delay)
    _run(["umount", "-l", mnt])  # lazy: detach now, finishes once nothing's using it


def detect_distro_name(disk_path: str) -> str:
    """Best-effort distro name off a disk, read from /etc/os-release
    (PRETTY_NAME) on whichever partition has one. Empty string if none
    found — a blank/foreign/Windows disk isn't an error, just unknown."""
    r = _run(["lsblk", "-lnpo", "NAME,TYPE", disk_path])
    parts = [line.split()[0] for line in r.stdout.splitlines()
             if len(line.split()) == 2 and line.split()[1] == "part"]
    with tempfile.TemporaryDirectory() as mnt:
        for part in parts:
            if _run(["mount", "-o", "ro", resolve_mountable(part), mnt]).returncode != 0:
                continue
            try:
                os_release = os.path.join(mnt, "etc", "os-release")
                if os.path.isfile(os_release):
                    with open(os_release, errors="replace") as f:
                        content = f.read()
                    m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', content, re.MULTILINE)
                    if m:
                        return m[1].strip()
            finally:
                _unmount_retry(mnt)
    return ""


def list_candidate_disks(exclude_disk_name: Optional[str] = None) -> list[Disk]:
    """Real disks (not the Ventoy stick), for either restore-destination or
    backup-source selection."""
    disks = []
    for path in _candidate_disk_paths():
        name = os.path.basename(path)
        if exclude_disk_name and name == exclude_disk_name:
            continue
        try:
            with open(f"/sys/block/{name}/size") as f:
                size_bytes = int(f.read().strip()) * 512
        except (OSError, ValueError):
            continue
        if size_bytes <= 0:
            continue
        disks.append(Disk(
            path=path,
            size_bytes=size_bytes,
            model=_disk_model(path),
            removable=_disk_removable(path),
            distro=detect_distro_name(path),
        ))
    return disks


def recommend_disk(disks: list[Disk]) -> Optional[Disk]:
    """Best guess at 'the internal disk to restore onto': largest
    non-removable one. Falls back to the largest disk overall if everything
    looks removable. This is the right heuristic for *restore* (you want
    the biggest free disk to grow into) but not for *backup* — see
    recommend_disk_for_backup."""
    if not disks:
        return None
    fixed = [d for d in disks if not d.removable]
    pool = fixed or disks
    return max(pool, key=lambda d: d.size_bytes)


def has_partitions(disk: "Disk") -> bool:
    r = _run(["lsblk", "-lnpo", "NAME,TYPE", disk.path])
    return any(line.split()[1] == "part" for line in r.stdout.splitlines() if len(line.split()) == 2)


def recommend_disk_for_backup(disks: list[Disk]) -> Optional[Disk]:
    """Best guess at 'the disk to back up': largest non-removable disk that
    actually *has something on it* (confirmed in testing: plain size-based
    recommendation can pick a blank spare/target disk over the disk that
    actually holds the OS, which is exactly backwards for backup). Falls
    back to plain recommend_disk() if no disk has partitions yet."""
    with_data = [d for d in disks if has_partitions(d)]
    return recommend_disk(with_data) or recommend_disk(disks)


def resolve_mountable(partition_path: str) -> str:
    """Real Ventoy sticks, booted through the full live desktop, turn out to
    wrap their data partition in a device-mapper node of the *same* base
    name (confirmed empirically: /dev/sdb1 comes back "already mounted or
    mount point busy" while the dm node mounts fine and shows the real
    contents) — apparently Ventoy's own runtime claims the raw partition.

    Prefer the mapper node when one exists — but confirmed live in testing
    that the /dev/mapper/<name> *symlink* is non-deterministic: `dmsetup ls`
    and the /dev/dm-N block device it names are always there the moment the
    dm target is created, but the udev rule that's supposed to symlink
    /dev/mapper/<name> to it doesn't always fire (seen it appear on some
    boots, never appear on others, `udevadm settle` doesn't help — it's not
    a pending-event race, the symlink is just never generated). Falling back
    to `dmsetup info` to resolve the real /dev/dm-N directly means this
    doesn't depend on that symlink ever showing up at all."""
    name = os.path.basename(partition_path)
    mapper_path = f"/dev/mapper/{name}"
    if os.path.exists(mapper_path):
        return mapper_path
    minor = _run(["dmsetup", "info", "-c", "--noheadings", "-o", "minor", name]).stdout.strip()
    if minor.isdigit():
        dm_path = f"/dev/dm-{minor}"
        if os.path.exists(dm_path):
            return dm_path
    return partition_path


def looks_like_ventoy_partition(mountpoint: str) -> bool:
    return (os.path.isdir(os.path.join(mountpoint, "restoreimg"))
            or os.path.isfile(os.path.join(mountpoint, "ventoy", "ventoy.json")))


def find_ventoy_partition(retries: int = 300, retry_delay: float = 1.0) -> Optional[str]:
    """Scan real partitions for Ventoy markers (restoreimg/, ventoy/ventoy.json).
    Returns the partition device path, or None.

    Retries on a transient mount failure: confirmed in testing that right at
    desktop/autostart time, the desktop's own automount daemon (udisks2/GVFS
    probing a freshly-inserted USB stick) can briefly hold the device, making
    a single mount attempt fail with "already mounted or busy" even though
    the very same mount succeeds moments later. This matters most for
    autostart, which fires at exactly that moment.

    Confirmed live (matters enough to call out): even 60 retries at 1s
    (~60s) wasn't always enough — at real autostart time the device-mapper
    node Ventoy wraps the partition in (see resolve_mountable) can itself
    take minutes to appear under load (toram is also busy copying the
    whole squashfs into RAM at the same moment), not just its
    /dev/mapper/<name> symlink; caught a real boot where `dmsetup ls`
    still didn't show it yet a full minute in, but
    resolve_mountable+find_ventoy_partition resolved it correctly the
    moment it was tried again later. The whole point of autostart is
    walking straight to disk selection with zero clicks, and this runs on
    a background thread behind a spinner — it costs nothing when the stick
    resolves in the usual 1-2s, so this errs very generous (300 retries,
    ~5 minutes worst case) rather than risk dumping the user into a raw
    filesystem browser they don't know how to use. Only a genuinely
    missing/broken stick ever waits out the full ceiling.

    A candidate already mounted somewhere is checked at its *existing*
    mountpoint rather than skipped outright: confirmed in testing that the
    backup GUI mounting the Ventoy partition at /mnt/ventoy and then exiting
    without unmounting (mounts outlive the process) made a subsequent
    restore-GUI launch fail to detect it at all — it's still mounted, so
    every mount attempt on it fails, and blindly skipping "already mounted"
    candidates meant the *only* Ventoy partition present was never even
    inspected."""
    candidates = []
    for pattern in ("/dev/sd*[0-9]", "/dev/nvme*p[0-9]*", "/dev/mmcblk*p[0-9]*"):
        candidates += glob.glob(pattern)
    candidates = sorted(candidates)

    with tempfile.TemporaryDirectory() as scan_mnt:
        for attempt in range(retries):
            for candidate in candidates:
                target = resolve_mountable(candidate)
                existing = _run(["findmnt", "-n", "-o", "TARGET", target])
                if existing.returncode == 0:
                    # Already mounted (e.g. a leftover mount from an earlier
                    # run of this or another tool) — check it in place.
                    mountpoint = existing.stdout.strip().splitlines()[0]
                    if looks_like_ventoy_partition(mountpoint):
                        return candidate
                    continue
                mount = _run(["mount", "-o", "ro", target, scan_mnt])
                if mount.returncode != 0:
                    continue
                try:
                    if looks_like_ventoy_partition(scan_mnt):
                        return candidate
                finally:
                    _unmount_retry(scan_mnt)
            if attempt < retries - 1:
                time.sleep(retry_delay)
    return None


def mount_ventoy_partition(data_part: str, mnt: str = "/mnt/ventoy") -> str:
    """Mount the Ventoy data partition at `mnt`, tolerating it already being
    mounted there (or elsewhere) from an earlier run — see the note on
    find_ventoy_partition about mounts outliving the process that made them.
    Returns the mountpoint that actually has the content (usually `mnt`)."""
    target = resolve_mountable(data_part)
    existing = _run(["findmnt", "-n", "-o", "TARGET", target])
    if existing.returncode == 0:
        return existing.stdout.strip().splitlines()[0]
    os.makedirs(mnt, exist_ok=True)
    _run(["mount", target, mnt])
    return mnt


def find_system_partition(disk: str) -> Optional[str]:
    """Identify the Linux root partition on `disk` by content, not by size
    or position: whichever partition has /etc/fstab + (/usr/bin or /bin)."""
    r = _run(["lsblk", "-lnpo", "NAME,TYPE", disk])
    parts = [line.split()[0] for line in r.stdout.splitlines()
             if len(line.split()) == 2 and line.split()[1] == "part"]

    with tempfile.TemporaryDirectory() as mnt:
        for part in parts:
            if _run(["mount", "-o", "ro", resolve_mountable(part), mnt]).returncode != 0:
                continue
            try:
                has_fstab = os.path.isfile(os.path.join(mnt, "etc", "fstab"))
                has_bin = (os.path.isdir(os.path.join(mnt, "usr", "bin"))
                           or os.path.isdir(os.path.join(mnt, "bin")))
                if has_fstab and has_bin:
                    return part
            finally:
                _unmount_retry(mnt)
    return None


_SFDISK_LINE = re.compile(
    r"^(?P<dev>/dev/[a-zA-Z0-9]+)\s*:.*start=\s*(?P<start>\d+),\s*size=\s*(?P<size>\d+)"
)


def _sfdisk_entries(disk: str) -> list[tuple[str, int, int]]:
    """[(dev, start_sector, end_sector_inclusive), ...] from `sfdisk -d`."""
    r = _run(["sfdisk", "-d", disk])
    entries = []
    for line in r.stdout.splitlines():
        m = _SFDISK_LINE.match(line)
        if m:
            start = int(m["start"])
            entries.append((m["dev"], start, start + int(m["size"]) - 1))
    return entries


def _grow_target_end_from_entries(entries: list[tuple[str, int, int]], part: str) -> Optional[str]:
    """parted resizepart END argument to grow `part` as far as it safely
    can: the disk's own end ("100%") if nothing at all follows it, or the
    exact sector just before whichever partition starts next otherwise.

    Confirmed live on a real machine (not a bug in growing itself — this
    function used to not exist at all): the old is_last_partition() check
    refused to grow anything unless it was *literally* the last partition
    on the disk, even when the space right after it was genuine unallocated
    free space with nothing in it — e.g. a swap partition placed after
    root, which is a completely ordinary layout, not an edge case. A real
    backup's own regrow-after-shrink step hit exactly this and gave up,
    leaving the source disk permanently smaller with a large chunk of now
    only-gparted-reachable free space. None if there's no room to grow
    into at all (this partition already reaches right up to the next one,
    or the next one starts at the very next sector)."""
    part_end = next((end for dev, start, end in entries if dev == part), None)
    if part_end is None:
        return None
    later_starts = [start for dev, start, end in entries if dev != part and start > part_end]
    if not later_starts:
        return "100%"
    next_start = min(later_starts)
    if next_start - part_end <= 1:
        return None
    return f"{next_start - 1}s"


def _grow_target_end(part: str, disk: str) -> Optional[str]:
    return _grow_target_end_from_entries(_sfdisk_entries(disk), part)


def get_source_min_bytes(restoreimg_path: str) -> Optional[int]:
    """Minimum destination size (bytes) implied by the backup's own saved
    partition table dump (e.g. restoreimg/vda-pt.sf). None if it can't tell
    at all (empty/unreadable dump).

    Confirmed live in testing: not every sfdisk -d dump includes a
    `last-lba:` summary line (depends on sfdisk version/options at backup
    time) — a dump with only per-partition start=/size= lines used to make
    this return None, silently turning off the destination-size check
    instead of blocking an undersized restore. Falls back to the furthest
    partition end (same start=/size= parsing is_last_partition already
    relies on) whenever last-lba is missing."""
    try:
        with open(os.path.join(restoreimg_path, "disk")) as f:
            diskname = f.read().strip()
        with open(os.path.join(restoreimg_path, f"{diskname}-pt.sf")) as f:
            sf = f.read()
    except OSError:
        return None
    sector_size_m = re.search(r"^sector-size:\s*(\d+)", sf, re.MULTILINE)
    sector_size = int(sector_size_m[1]) if sector_size_m else 512

    last_lba = re.search(r"^last-lba:\s*(\d+)", sf, re.MULTILINE)
    if last_lba:
        return (int(last_lba[1]) + 1) * sector_size
    # _SFDISK_LINE's ^ anchor is per-line (matched line-by-line elsewhere, e.g.
    # _sfdisk_entries) rather than re.MULTILINE — walk sf the same way.
    matches = (_SFDISK_LINE.match(line) for line in sf.splitlines())
    ends = [int(m["start"]) + int(m["size"]) for m in matches if m]
    return max(ends) * sector_size if ends else None


# ---------------------------------------------------------------------------
# Restore + grow
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    ok: bool
    message: str = ""
    # Set True only by shrink_system_partition_for_backup (and its helpers)
    # when they actually modified something (filesystem and/or partition
    # size). Every other StepResult leaves this False. backup() uses it to
    # decide whether the source disk needs to be grown back afterward —
    # skipping "already small enough"/unsupported-fs no-ops, which never
    # touched the disk and must be left exactly as found.
    changed: bool = False


# Rough per-stage progress mapping, shared by the GTK and curses front-ends —
# the CLI output is verbose but not machine-progress-annotated, so this is a
# coarse "which phase" indicator rather than a byte-accurate percentage.
# Markers that don't appear for a given operation are simply never matched.
STAGE_MARKERS = [
    ("Restoring the first", 0.15),
    ("restored partition table", 0.30),
    ("Cloned successfully", 0.55),
    ("Growing filesytem", 0.70),
    ("Growing partition", 0.70),
    ("Resizing filesystem", 0.80),
    ("resized successfully", 0.95),
    ("Checksumming", 0.85),
    ("Backup operation", 0.95),
]


# ---------------------------------------------------------------------------
# Fine-grained stage/percentage tracking (GTK GUI only — see Stage below).
# TUI keeps using the flat STAGE_MARKERS list above; not touched here.
# ---------------------------------------------------------------------------

@dataclass
class PartcloneProgress:
    elapsed_s: int
    remaining_s: int
    percent: float
    rate: str = ""  # e.g. "6.94GB/min" — empty when partclone hasn't computed one yet


# partclone's own live progress line, confirmed against real captured output
# (vmtest/serial.log), e.g.:
#   "Elapsed: 00:00:01, Remaining: 00:01:39, Completed:   1.00%,   0.00byte/min,"
#   "Elapsed: 00:00:02, Remaining: 00:00:00, Completed: 100.00%, Rate:   6.94GB/min,"
# The "Rate:" label itself is sometimes missing (first line, before partclone
# has computed one) — the regex tolerates that.
_PARTCLONE_RE = re.compile(
    r"Elapsed:\s*(\d+):(\d+):(\d+),\s*Remaining:\s*(\d+):(\d+):(\d+),\s*"
    r"Completed:\s*([\d.]+)%,\s*(?:Rate:\s*)?([\d.]+[^\s,]*)"
)
# Fallback for tools with no elapsed/remaining/rate, just a trailing percentage
# (e.g. `resize2fs -p`) — used only to drive a stage's own %, no speed/ETA.
_PERCENT_ONLY_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# resize2fs -p's own progress format, confirmed live on a real shrink —
# turns out to be a fixed-width dash/X text bar, no digit/percent text at
# all ("Relocating blocks             ----------XXXXXXXXXX"), so
# _PERCENT_ONLY_RE never matches any of it and the stage bar just sat at
# 0% the whole time despite the log clearly moving. {10,} is a floor
# against matching some unrelated short run of dashes by accident.
_RESIZE2FS_BAR_RE = re.compile(r"^[A-Za-z][\w ]*?\s{2,}([-X]{10,})\s*$")


def _hms_to_seconds(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_partclone_progress(line: str) -> Optional[PartcloneProgress]:
    m = _PARTCLONE_RE.search(line)
    if not m:
        return None
    return PartcloneProgress(
        elapsed_s=_hms_to_seconds(*m.group(1, 2, 3)),
        remaining_s=_hms_to_seconds(*m.group(4, 5, 6)),
        percent=float(m.group(7)),
        rate=m.group(8),
    )


def parse_percent_only(line: str) -> Optional[float]:
    """Last-resort percent extractor for tools that print a bare 'NN.N%'
    with no elapsed/remaining/rate fields, or (confirmed live) resize2fs
    -p's dash/X text bar instead of any digit percentage at all."""
    m = _PERCENT_ONLY_RE.search(line)
    if m:
        return float(m[1])
    bar = _RESIZE2FS_BAR_RE.match(line)
    if bar:
        filled = bar[1]
        return 100.0 * filled.count("X") / len(filled)
    return None


@dataclass
class Stage:
    label: str
    start_marker: str   # substring in the log that begins this stage
    measurable: bool     # True if the tool driving it reports a live percentage
    weight: float         # this stage's share of the overall progress bar


# Ordered restore stages. "Restoring partitions" covers every per-partition
# partclone.restore call — its own live % resets at each new "Restoring
# Partition N:" line rather than tracking a partition count up front.
RESTORE_STAGES = [
    # "$ rescuezilla restore ..." is the literal argv echo run_streaming
    # prints for every command it runs (log(f"$ {' '.join(args)}")) — since
    # we build that argv ourselves, this line is 100% under our control and
    # can never be reworded by a future rescuezilla release the way its own
    # wrapper prose ("Restoring the first...") could be.
    Stage("Restoring partition table", "$ rescuezilla restore", measurable=False, weight=0.05),
    Stage("Restoring partitions", "Restoring Partition", measurable=True, weight=0.60),
    Stage("Growing partition", "Growing partition", measurable=False, weight=0.10),
    Stage("Resizing filesystem", "Resizing filesystem", measurable=True, weight=0.20),
    # "Successfully restored image partition" (the run_streaming success_marker)
    # fires *before* grow/resize, not after — chronologically it belongs inside
    # "Restoring partitions" above, not as a final stage. The real last thing
    # that happens is check_restored_filesystem's own log line.
    Stage("Finishing up", "Sanity-checking", measurable=False, weight=0.05),
]

# Ordered backup stages. "Restoring source size" only actually runs when the
# pre-backup shrink changed something (StepResult.changed) — the GUI marks it
# "skipped" rather than stuck-pending when a run finishes without ever hitting
# its start marker.
BACKUP_STAGES = [
    Stage("Shrinking partition", "Shrinking filesystem", measurable=True, weight=0.10),
    # Same "$ <argv>" echo trick as restore's first stage, and for the same
    # reason: an earlier guess here based on the wrapper's own per-partition
    # prose ("Backing up Partition", by wrong-guessed symmetry with
    # restore's confirmed "Restoring Partition N:" text) simply never
    # matched real output — confirmed live, the actual line is "Backup
    # /dev/sdaN containing filesystem ext4 to .../sdaN.ext4-ptcl-img.gz."
    # — leaving the stage tracker stuck on "Shrinking partition" for the
    # entire backup no matter how far partclone actually got. Matching our
    # own echoed argv instead needs no wrapper-wording guess at all, and
    # fires right as the backup command starts (a beat before the first
    # partition's own line, which reads fine — backup's own hardware-info
    # gathering happens first anyway).
    Stage("Backing up partitions", "$ rescuezilla backup", measurable=True, weight=0.65),
    Stage("Checksumming", "Checksumming", measurable=False, weight=0.10),
    Stage("Restoring source size", "Restoring the source disk's partition", measurable=True, weight=0.15),
]


def check_destination_size(restoreimg_path: str, disk: Disk) -> StepResult:
    min_bytes = get_source_min_bytes(restoreimg_path)
    if min_bytes is None:
        return StepResult(True, "Could not verify destination size against the backup (older backup format?).")
    if disk.size_bytes < min_bytes:
        needed = min_bytes // (1024 * 1024)
        have = disk.size_bytes // (1024 * 1024)
        return StepResult(False, f"{disk.path} is smaller than the backed-up disk ({have}MiB < {needed}MiB).")
    return StepResult(True)


TAIL_LINES = 12  # how much context to fold into a failure message — see run_streaming


class OperationAborted(Exception):
    """Raised out of run_streaming when abort_check() fires mid-command —
    caught once at the top of restore()/backup() and turned into a StepResult.
    A best-effort mid-write cancel, not a clean rollback: whatever the killed
    command was doing (writing a partition, resizing a filesystem) is left
    exactly as far as it got."""


def run_streaming(args, log: LogFn, success_marker: str = None, stdin_text: str = None,
                   abort_check: Callable[[], Optional[str]] = None) -> tuple[int, list[str]]:
    """Run a command, streaming stdout+stderr line-by-line into `log`.
    Returns (exit_code, last_few_lines) — the tail is for building a
    specific failure message (verbose, not just "it failed") without the
    caller having to re-capture output itself. exit_code is 0 whenever
    `success_marker` appeared anywhere in the output, regardless of the
    real exit code.

    `stdin_text`, when given, is written to the process's stdin and closed
    — needed for e.g. `parted resizepart` shrinking, which prompts a
    "Shrinking a partition can cause data loss, continue?" confirmation
    that `-s` (script mode) does NOT suppress (confirmed in testing: with
    no tty attached it silently defaults to "No" and the resize is a
    no-op) — without answering it explicitly the filesystem shrinks but
    the partition itself never does.

    The marker override works around a real bug in stock Rescuezilla's own
    `rescuezilla` bash wrapper (confirmed in testing, not our code): after a
    genuinely successful restore, its cleanup path can call
    `systemctl --user --runtime unmask --quiet --` with no unit names, which
    systemctl rejects ("Too few arguments."), and that becomes the wrapper's
    own exit code — silently turning a successful restore into an apparent
    failure. The restore engine's own "Successfully restored image
    partition" line is the trustworthy signal; the wrapper's exit code
    isn't, on its own.

    Reads stdout on a background thread and waits on the *process* (proc.wait()
    tracks the PID directly) rather than on pipe EOF: confirmed in testing
    that the wrapper's own udev calls (`rm -f .../64-md-raid-assembly.rules`,
    `systemctl ... unmask`) can leave the stdout pipe's write end held open
    by some unrelated long-lived process it briefly touches (systemd-udevd),
    so `for line in proc.stdout` never sees EOF even though rescuezilla
    itself finished and exited — that hung our whole app indefinitely.
    Waiting on the real process instead, then giving the reader a couple
    seconds to drain, fixes it without caring who else might be holding
    stdout open.

    `abort_check`, when given, is polled roughly every 0.3s from a separate
    watcher thread (so it fires even during a long stretch with no output,
    e.g. a slow btrfs resize) — a truthy return value kills the process and
    raises OperationAborted with that value as the reason."""
    log(f"$ {' '.join(args)}")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.PIPE if stdin_text is not None else None,
                             text=True, bufsize=1)
    if stdin_text is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    saw_marker = False
    aborted_reason = None
    tail = deque(maxlen=TAIL_LINES)

    def reader():
        nonlocal saw_marker
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                log(stripped)
                tail.append(stripped)
                if success_marker and success_marker in line:
                    saw_marker = True
        except (ValueError, OSError):
            pass  # stdout got closed out from under us while draining below — fine, we're done either way

    def watcher():
        nonlocal aborted_reason
        while proc.poll() is None:
            reason = abort_check()
            if reason:
                aborted_reason = reason
                proc.kill()
                return
            time.sleep(0.3)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    watcher_thread = None
    if abort_check:
        watcher_thread = threading.Thread(target=watcher, daemon=True)
        watcher_thread.start()
    proc.wait()
    reader_thread.join(timeout=2)  # drain whatever output was already buffered
    if watcher_thread:
        watcher_thread.join(timeout=1)
    try:
        proc.stdout.close()  # unstick the reader thread if something else is still holding the pipe open
    except OSError:
        pass

    if aborted_reason:
        raise OperationAborted(aborted_reason)
    rc = 0 if saw_marker else proc.returncode
    return rc, list(tail)


def _fail_with_tail(headline: str, tail: list[str]) -> StepResult:
    """Build a verbose failure message: the headline plus the last few
    lines of actual command output, so 'it failed' always comes with real
    detail instead of a bare generic sentence."""
    if tail:
        return StepResult(False, headline + "\n\nLast output:\n" + "\n".join(tail))
    return StepResult(False, headline)


def _make_abort_check(cancel_event: Optional[threading.Event]) -> Optional[Callable[[], Optional[str]]]:
    """Turn a plain threading.Event into the abort_check callback run_streaming
    expects — shared by every cancellable step below so "check cancel_event"
    logic exists exactly once."""
    if not cancel_event:
        return None
    return lambda: "Cancelled by user" if cancel_event.is_set() else None


def grow_system_partition(disk: str, log: LogFn = _noop_log,
                           cancel_event: Optional[threading.Event] = None) -> StepResult:
    abort_check = _make_abort_check(cancel_event)
    sys_part = find_system_partition(disk)
    if not sys_part:
        return StepResult(False, "Could not identify a Linux system partition — restore succeeded, "
                                  "but auto-grow was skipped. You can grow partitions manually with gparted.")
    log(f"Identified Linux system partition: {sys_part}")

    target_end = _grow_target_end(sys_part, disk)
    if target_end is None:
        return StepResult(False, f"{sys_part} has no free space after it on {disk} to grow into — "
                                  "restore succeeded, but there's nothing extra to reclaim.")

    partition_num = sys_part[len(disk):].lstrip("p")
    log(f"Growing partition {partition_num} ({sys_part}) to use all available space...")
    rc, tail = run_streaming(["parted", "-s", "--", disk, "resizepart", partition_num, target_end], log,
                              abort_check=abort_check)
    if rc != 0:
        return _fail_with_tail(f"Failed to resize partition {sys_part}.", tail)

    _run(["partprobe", disk])
    r = _run(["blkid", "-o", "value", "-s", "TYPE", sys_part])
    fs_type = r.stdout.strip()
    log(f"Resizing filesystem on {sys_part} (detected: {fs_type})...")

    if fs_type in ("ext2", "ext3", "ext4"):
        run_streaming(["e2fsck", "-f", "-p", sys_part], log, abort_check=abort_check)
        # -p: print percentage completion, so the GUI can track this stage live too.
        rc, tail = run_streaming(["resize2fs", "-p", sys_part], log, abort_check=abort_check)
        ok = rc == 0
    elif fs_type == "btrfs":
        with tempfile.TemporaryDirectory() as tmp:
            if _run(["mount", sys_part, tmp]).returncode == 0:
                rc, tail = run_streaming(["btrfs", "filesystem", "resize", "max", tmp], log,
                                          abort_check=abort_check)
                ok = rc == 0
            else:
                ok, tail = False, []
            _unmount_retry(tmp)
    elif fs_type == "xfs":
        with tempfile.TemporaryDirectory() as tmp:
            if _run(["mount", sys_part, tmp]).returncode == 0:
                rc, tail = run_streaming(["xfs_growfs", tmp], log, abort_check=abort_check)
                ok = rc == 0
            else:
                ok, tail = False, []
            _unmount_retry(tmp)
    else:
        return StepResult(False, f"Unsupported filesystem type '{fs_type}' — partition grown, filesystem not resized.")

    if ok:
        return StepResult(True, "Filesystem resized successfully.")
    return _fail_with_tail("Partition was resized but filesystem resize failed.", tail)


def check_restored_filesystem(disk: str, log: LogFn = _noop_log) -> StepResult:
    """Post-restore sanity check: re-identify the system partition and run a
    read-only filesystem check on it, so a corrupt clone is reported instead
    of silently trusted. Never touches anything (-n/--readonly throughout) —
    this is a report, not a repair."""
    sys_part = find_system_partition(disk)
    if not sys_part:
        return StepResult(True, "Could not identify a Linux system partition to sanity-check.")
    fs_type = _run(["blkid", "-o", "value", "-s", "TYPE", sys_part]).stdout.strip()
    log(f"Sanity-checking {sys_part} (detected: {fs_type})...")

    if fs_type in ("ext2", "ext3", "ext4"):
        rc, tail = run_streaming(["e2fsck", "-fn", sys_part], log)
    elif fs_type == "btrfs":
        rc, tail = run_streaming(["btrfs", "check", "--readonly", sys_part], log)
    elif fs_type == "xfs":
        rc, tail = run_streaming(["xfs_repair", "-n", sys_part], log)
    else:
        return StepResult(True, f"Unsupported filesystem type '{fs_type}' — skipping post-restore check.")

    if rc == 0:
        return StepResult(True, f"Post-restore check: {sys_part} looks clean.")
    return _fail_with_tail(f"Post-restore check found problems on {sys_part} — the restore likely still "
                            "worked, but verify manually (e.g. with gparted) before relying on this disk.", tail)


SHRINK_TARGET_BYTES = 20 * 1024 * 1024 * 1024  # 20G — see shrink_system_partition_for_backup
SHRINK_MARGIN_FRACTION = 0.15  # headroom above the filesystem's reported minimum
SHRINK_MARGIN_MIN_BYTES = 512 * 1024 * 1024
PARTITION_END_BUFFER_BYTES = 16 * 1024 * 1024  # alignment/safety slack past the shrunk filesystem


def shrink_system_partition_for_backup(disk: str, log: LogFn = _noop_log,
                                        cancel_event: Optional[threading.Event] = None) -> StepResult:
    """Before backing a disk up, shrink its Linux system partition to 20G —
    or as close to that as the filesystem's actual data allows, whichever
    is bigger — so the backup image is smaller and faster (this is the
    other half of the restore-side auto-grow: shrink for a fast/small
    backup, grow back to fill whatever disk it's restored onto).

    Always non-fatal: if the partition can't be identified, is already
    small enough, or is a filesystem type that can't be safely shrunk here
    (XFS doesn't support shrinking at all; anything unrecognised is left
    alone rather than guessed at), the backup proceeds at its current size
    — this is an optimization, not a requirement."""
    sys_part = find_system_partition(disk)
    if not sys_part:
        return StepResult(True, "Could not identify a Linux system partition — skipping pre-backup shrink.")
    log(f"Identified Linux system partition: {sys_part}")

    current_bytes = int(_run(["blockdev", "--getsize64", sys_part]).stdout.strip() or 0)
    if current_bytes and current_bytes <= SHRINK_TARGET_BYTES:
        return StepResult(True, f"{sys_part} is already {current_bytes // (1024**3)}G — no shrink needed.")

    fs_type = _run(["blkid", "-o", "value", "-s", "TYPE", sys_part]).stdout.strip()

    if fs_type in ("ext2", "ext3", "ext4"):
        return _shrink_ext(sys_part, disk, log, cancel_event=cancel_event)
    if fs_type == "btrfs":
        return _shrink_btrfs(sys_part, disk, log, cancel_event=cancel_event)
    if fs_type == "xfs":
        return StepResult(True, "XFS does not support shrinking — skipping pre-backup shrink.")
    return StepResult(True, f"Unsupported filesystem type '{fs_type}' — skipping pre-backup shrink.")


def _ext4_minimum_bytes(sys_part: str) -> Optional[int]:
    """Minimum size (bytes) `resize2fs` reports an ext2/3/4 filesystem could
    shrink to, i.e. roughly how much real data is on it. Shared by the actual
    shrink (_shrink_ext) and by check_backup_space's dry-run estimate — same
    regex/block-size logic, computed once."""
    _, tail = run_streaming(["resize2fs", "-P", sys_part], _noop_log)
    m = re.search(r"minimum size.*?:\s*(\d+)", "\n".join(tail), re.IGNORECASE)
    if not m:
        return None
    block_size = 4096
    bs = _run(["dumpe2fs", "-h", sys_part])
    bm = re.search(r"^Block size:\s*(\d+)", bs.stdout, re.MULTILINE)
    if bm:
        block_size = int(bm[1])
    return int(m[1]) * block_size


LOW_SPACE_FLOOR_BYTES = 256 * 1024 * 1024  # abort a backup if the destination drops below this — see backup()


def check_backup_space(source_disk: str, destination_folder: str) -> StepResult:
    """Pre-flight check mirroring check_destination_size: does the source
    disk's actual data plausibly fit in whatever free space the destination
    has? Only ext2/3/4 can be estimated cheaply (see _ext4_minimum_bytes) —
    btrfs/xfs/unknown fall back to "can't verify", same non-blocking pattern
    check_destination_size uses for an unrecognised backup format."""
    sys_part = find_system_partition(source_disk)
    if not sys_part:
        return StepResult(True, "Could not verify backup size (no Linux system partition identified).")
    fs_type = _run(["blkid", "-o", "value", "-s", "TYPE", sys_part]).stdout.strip()
    if fs_type not in ("ext2", "ext3", "ext4"):
        return StepResult(True, "Could not verify backup size against free space (unsupported filesystem type).")

    minimum_bytes = _ext4_minimum_bytes(sys_part)
    if minimum_bytes is None:
        return StepResult(True, "Could not verify backup size against free space (couldn't read filesystem usage).")
    margin = max(int(minimum_bytes * SHRINK_MARGIN_FRACTION), SHRINK_MARGIN_MIN_BYTES)
    needed_bytes = minimum_bytes + margin

    dest_check_path = destination_folder if os.path.isdir(destination_folder) else (
        os.path.dirname(destination_folder.rstrip("/")) or "/")
    try:
        free_bytes = shutil.disk_usage(dest_check_path).free
    except OSError:
        return StepResult(True, "Could not verify free space at the backup destination.")

    if free_bytes < needed_bytes:
        needed = needed_bytes // (1024 * 1024)
        have = free_bytes // (1024 * 1024)
        return StepResult(False, f"Not enough free space at the backup destination for {sys_part} "
                                  f"({have}MiB free < ~{needed}MiB needed).")
    return StepResult(True)


def _shrink_partition_to(sys_part: str, disk: str, new_fs_bytes: int, log: LogFn) -> bool:
    """Shrink the partition itself (not the filesystem) to just past
    `new_fs_bytes`, via sfdisk. Returns whether it worked.

    Confirmed in testing: `parted -s ... resizepart` hard-refuses its own
    "Shrinking a partition can cause data loss, continue?" prompt no matter
    what's piped to stdin (even with `---pretend-input-tty` + "yes") — it's
    seemingly not a bypassable confirmation in script mode at all. sfdisk's
    `-N <num>` (change just one partition's size, keeping its start) has no
    such gate and resizes cleanly non-interactively."""
    partition_num = sys_part[len(disk):].lstrip("p")
    new_size_mib = (new_fs_bytes + PARTITION_END_BUFFER_BYTES) // (1024 * 1024)
    log(f"Shrinking partition {partition_num} to {new_size_mib}MiB...")
    rc, tail = run_streaming(["sfdisk", "-N", partition_num, "--no-reread", disk], log,
                              stdin_text=f", {new_size_mib}M\n")
    if rc != 0:
        log("Failed to shrink the partition (filesystem was already shrunk — backup will just see extra "
            "unused space, nothing is at risk).")
        return False
    _run(["partprobe", disk])
    return True


def _shrink_ext(sys_part: str, disk: str, log: LogFn,
                 cancel_event: Optional[threading.Event] = None) -> StepResult:
    abort_check = _make_abort_check(cancel_event)
    run_streaming(["e2fsck", "-f", "-p", sys_part], log, abort_check=abort_check)  # resize2fs requires a clean fs

    minimum_bytes = _ext4_minimum_bytes(sys_part)
    if minimum_bytes is None:
        return StepResult(True, f"Couldn't determine the minimum size of {sys_part} — skipping pre-backup shrink.")

    margin = max(int(minimum_bytes * SHRINK_MARGIN_FRACTION), SHRINK_MARGIN_MIN_BYTES)
    # 20G if the data fits with margin, else as small as it can safely go —
    # but never larger than what's there now; this function only shrinks.
    current_bytes = int(_run(["blockdev", "--getsize64", sys_part]).stdout.strip() or 0)
    target_bytes = min(max(SHRINK_TARGET_BYTES, minimum_bytes + margin), current_bytes or SHRINK_TARGET_BYTES)
    if current_bytes and target_bytes >= current_bytes:
        return StepResult(True, f"{sys_part} is already close to its minimum size — no shrink needed.")

    log(f"Shrinking filesystem on {sys_part} to {target_bytes // (1024**3)}G "
        f"(minimum possible: {minimum_bytes // (1024**3)}G)...")
    # -p: print percentage completion, so the GUI can track this stage live too.
    rc, tail = run_streaming(["resize2fs", "-p", sys_part, f"{target_bytes // (1024*1024)}M"], log,
                              abort_check=abort_check)
    if rc != 0:
        return _fail_with_tail(f"Could not shrink the filesystem on {sys_part} — proceeding at its current size.",
                                tail)

    _shrink_partition_to(sys_part, disk, target_bytes, log)
    return StepResult(True, f"Shrunk {sys_part} to ~{target_bytes // (1024**3)}G before backup.", changed=True)


def _shrink_btrfs(sys_part: str, disk: str, log: LogFn,
                   cancel_event: Optional[threading.Event] = None) -> StepResult:
    # btrfs has no direct "minimum size" query; attempt the 20G target
    # directly and accept that btrfs itself refuses if actual data won't
    # fit — that failure is expected and not a real problem.
    with tempfile.TemporaryDirectory() as tmp:
        if _run(["mount", sys_part, tmp]).returncode != 0:
            return StepResult(True, f"Could not mount {sys_part} to shrink it — skipping pre-backup shrink.")
        try:
            rc, tail = run_streaming(["btrfs", "filesystem", "resize",
                                       str(SHRINK_TARGET_BYTES // (1024*1024)) + "M", tmp], log,
                                      abort_check=_make_abort_check(cancel_event))
        finally:
            _unmount_retry(tmp)
    if rc != 0:
        return StepResult(True, f"{sys_part} likely has more than 20G of data — leaving it at its current size.")
    _shrink_partition_to(sys_part, disk, SHRINK_TARGET_BYTES, log)
    return StepResult(True, f"Shrunk {sys_part} to ~20G before backup.", changed=True)


def restore(restoreimg_path: str, disk: str, log: LogFn = _noop_log,
            cancel_event: Optional[threading.Event] = None) -> StepResult:
    log(f"Restoring {restoreimg_path} to {disk}...")
    try:
        rc, tail = run_streaming(["rescuezilla", "restore", "--source", restoreimg_path,
                                  "--destination", disk, "--overwrite-partition-table"], log,
                                 success_marker="Successfully restored image partition",
                                 abort_check=_make_abort_check(cancel_event))
    except OperationAborted as exc:
        return StepResult(False, f"Restore cancelled ({exc}). The disk may be left partially written — "
                                  "do not treat this as a working system.")
    if rc != 0:
        return _fail_with_tail(
            "Restore failed. The disk may be left partially written — do not treat this as a working system.",
            tail)
    log("Restore reported success.")
    _run(["partprobe", disk])
    # Confirmed live in testing: partprobe returning doesn't guarantee the
    # kernel/udev have actually finished creating the new partition device
    # nodes yet — grow_system_partition's find_system_partition() call right
    # after this raced that and came back empty-handed ("Could not identify
    # a Linux system partition"), even though the exact same call succeeded
    # moments later in check_restored_filesystem(). udevadm settle blocks
    # until pending udev events are processed, closing the race.
    _run(["udevadm", "settle", "--timeout=10"])

    try:
        grow = grow_system_partition(disk, log, cancel_event=cancel_event)
    except OperationAborted as exc:
        return StepResult(True, f"Restore succeeded, but growing the partition was cancelled ({exc}) — "
                                 "you can grow it manually with gparted.")
    message = ("Restore done, system partition grown to fill the disk." if grow.ok
               else f"Restore succeeded, but: {grow.message}")

    check = check_restored_filesystem(disk, log)
    message += f"\n\n{check.message}" if not check.ok else f" {check.message}"
    return StepResult(True, message)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def describe_disk_for_backup(disk: "Disk") -> str:
    """A single safe token for --description: rescuezilla's CLI wrapper
    mis-splits a multi-word description (confirmed in Phase 1 testing), so
    collapse the detected distro name (spaces and all) into one word rather
    than dropping it."""
    return re.sub(r"[^A-Za-z0-9]+", "_", disk.distro).strip("_") if disk.distro else ""


def _make_backup_abort_check(cancel_event: Optional[threading.Event],
                              destination_folder: str) -> Callable[[], Optional[str]]:
    """abort_check for the main backup command: cancel button OR the
    destination running low on space mid-write (checked once per output
    line — see run_streaming's watcher). destination_folder may not exist
    yet (rescuezilla creates it), so free space is checked on its parent."""
    check_path = destination_folder if os.path.isdir(destination_folder) else (
        os.path.dirname(destination_folder.rstrip("/")) or "/")

    def check() -> Optional[str]:
        if cancel_event and cancel_event.is_set():
            return "Cancelled by user"
        try:
            free = shutil.disk_usage(check_path).free
        except OSError:
            return None
        if free < LOW_SPACE_FLOOR_BYTES:
            return f"destination ran low on space (under {LOW_SPACE_FLOOR_BYTES // (1024*1024)}MiB free)"
        return None

    return check


def _try_regrow_after_cancel(source_disk: str, log: LogFn) -> None:
    """Best-effort attempt to restore the source disk's partition to full
    size after a cancel landed mid-shrink, where we don't know how far the
    shrink actually got. Safe to call even if nothing was shrunk at all —
    grow_system_partition is a no-op on an already-full-size partition.
    ponytail: doesn't try to resume the *specific* shrink step that was
    interrupted, just re-grows from scratch; good enough since shrink/grow
    are already idempotent in each direction."""
    try:
        grow_system_partition(source_disk, log)
    except OperationAborted:
        pass  # user is cancelling everything; don't fight them on the regrow too


def backup(source_disk: str, destination_folder: str, log: LogFn = _noop_log,
           description: str = "", cancel_event: Optional[threading.Event] = None) -> StepResult:
    try:
        shrink = shrink_system_partition_for_backup(source_disk, log, cancel_event=cancel_event)
    except OperationAborted as exc:
        _try_regrow_after_cancel(source_disk, log)
        return StepResult(False, f"Backup cancelled before it started ({exc}).")
    log(shrink.message)

    args = ["rescuezilla", "backup", "--source", source_disk,
            "--destination", destination_folder, "--compression-format", "gzip"]
    # Note: rescuezilla's CLI wrapper mis-splits a multi-word --description
    # (verified in Phase 1 testing) — only pass it if it's a single token.
    if description and " " not in description:
        args += ["--description", description]
    try:
        rc, tail = run_streaming(args, log, abort_check=_make_backup_abort_check(cancel_event, destination_folder))
    except OperationAborted as exc:
        result = StepResult(False, f"Backup cancelled ({exc}). The backup at {destination_folder} "
                                    "is incomplete — delete it before trying again.")
    else:
        # Same stock-wrapper exit-code bug as restore() (confirmed in testing):
        # a real backup can finish (clonezilla-img written and checksummed) and
        # still return nonzero because of the wrapper's own unrelated cleanup
        # bug. Checking the actual output file is simpler and more robust here
        # than hunting for a specific log line.
        image_file = os.path.join(destination_folder, "clonezilla-img")
        if rc != 0 and not (os.path.isfile(image_file) and os.path.getsize(image_file) > 0):
            result = _fail_with_tail("Backup failed.", tail)
        else:
            result = StepResult(True, "Backup completed successfully.")

    # The shrink above is only ever meant to make a smaller/faster backup —
    # it must never leave the machine we just backed up any different than
    # we found it. Only run this when something was actually shrunk (a
    # no-op shrink, e.g. "already small enough", never touched the disk and
    # must be left alone) — regardless of whether the backup itself
    # succeeded, since we still altered the source disk either way.
    if shrink.changed:
        log("Restoring the source disk's partition to its original size...")
        try:
            regrow = grow_system_partition(source_disk, log, cancel_event=cancel_event)
        except OperationAborted as exc:
            warning = (f"IMPORTANT: cancelled while restoring the source disk's original size ({exc}). "
                       f"The source machine's disk is now smaller than before this backup — grow it back "
                       f"manually (e.g. with gparted) before considering this done.")
            log(warning)
            return StepResult(False, (result.message + "\n\n" + warning).strip())
        if regrow.ok:
            log("Source disk restored to its original size.")
        else:
            warning = (f"IMPORTANT: the source disk's partition could NOT be restored to its "
                       f"original size after backup ({regrow.message}). The source machine's disk "
                       f"is now smaller than before this backup — grow it back manually (e.g. with "
                       f"gparted) before considering this done.")
            log(warning)
            result = StepResult(result.ok, (result.message + "\n\n" + warning).strip())

    return result


def _self_check():
    """No block-device access needed — covers the pure-logic parts that the
    GUI's progress tracker depends on: progress-line parsing, stage weights
    summing to 1.0, and run_streaming's cancel mechanism actually killing a
    running process. Run directly: `python3 pnlug_rescue_lib.py`."""
    p = parse_partclone_progress("Elapsed: 00:00:01, Remaining: 00:01:39, Completed:   1.00%,   0.00byte/min,")
    assert p and p.elapsed_s == 1 and p.remaining_s == 99 and p.percent == 1.0 and p.rate == "0.00byte/min", p

    p2 = parse_partclone_progress("Elapsed: 00:00:02, Remaining: 00:00:00, Completed: 100.00%, Rate:   6.94GB/min,")
    assert p2 and p2.percent == 100.0 and p2.rate == "6.94GB/min", p2

    assert parse_partclone_progress("just some unrelated log line") is None
    assert parse_percent_only("   50.0%") == 50.0
    assert parse_percent_only("no percent here") is None
    # Real line captured off a live resize2fs -p run — half-filled bar.
    half_bar = "Relocating blocks             " + "-" * 40 + "X" * 40
    assert parse_percent_only(half_bar) == 50.0, parse_percent_only(half_bar)
    full_bar = "Scanning inode table          " + "X" * 80
    assert parse_percent_only(full_bar) == 100.0
    assert parse_percent_only("Begin pass 2 (max = 3035716)") is None

    # Exact real layout from a live machine: root (sda2) with swap (sda3)
    # placed right after it — root2 is not the *last* partition, but there
    # was a huge unallocated gap after it that the old is_last_partition
    # check refused to touch at all.
    real_entries = [
        ("/dev/sda1", 4096, 4096 + 614400 - 1),
        ("/dev/sda2", 618496, 618496 + 41975808 - 1),
        ("/dev/sda3", 461674273, 461674273 + 26722829 - 1),
    ]
    assert _grow_target_end_from_entries(real_entries, "/dev/sda2") == "461674272s"
    # Genuinely last partition -> grow to the disk's own end.
    assert _grow_target_end_from_entries(real_entries, "/dev/sda3") == "100%"
    # No entries at all for the requested partition.
    assert _grow_target_end_from_entries(real_entries, "/dev/sda9") is None
    # Already touching the next partition — nothing to grow into.
    tight_entries = [("/dev/sda1", 0, 99), ("/dev/sda2", 100, 199)]
    assert _grow_target_end_from_entries(tight_entries, "/dev/sda1") is None

    for name, stages in (("RESTORE_STAGES", RESTORE_STAGES), ("BACKUP_STAGES", BACKUP_STAGES)):
        total = sum(s.weight for s in stages)
        assert abs(total - 1.0) < 1e-9, f"{name} weights sum to {total}, not 1.0"

    # Stage markers that use our own argv echo must actually match the real
    # line run_streaming prints for that exact command — this is the whole
    # point of using them over the wrapper's own (guessable, changeable)
    # prose, so a regression here would be exactly the kind of silent
    # stage-tracker breakage this self-check exists to catch.
    restore_cmd_line = "$ " + " ".join(
        ["rescuezilla", "restore", "--source", "/mnt/ventoy/restoreimg",
         "--destination", "/dev/sda", "--overwrite-partition-table"])
    assert RESTORE_STAGES[0].start_marker in restore_cmd_line
    backup_cmd_line = "$ " + " ".join(
        ["rescuezilla", "backup", "--source", "/dev/sda",
         "--destination", "/mnt/ventoy/restoreimg", "--compression-format", "gzip"])
    assert BACKUP_STAGES[1].start_marker in backup_cmd_line

    cancel_event = threading.Event()
    threading.Timer(0.3, cancel_event.set).start()
    lines = []
    try:
        run_streaming(["bash", "-c", "for i in $(seq 1 20); do echo tick $i; sleep 0.2; done"],
                       lines.append, abort_check=_make_abort_check(cancel_event))
        raise AssertionError("expected OperationAborted, cancel was never honored")
    except OperationAborted:
        pass
    assert len(lines) < 15, f"cancel should have cut this off well before 20 ticks, got {len(lines)}"

    print("pnlug_rescue_lib self-check OK")


if __name__ == "__main__":
    _self_check()
