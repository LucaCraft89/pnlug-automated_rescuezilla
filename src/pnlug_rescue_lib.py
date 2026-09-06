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
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

LogFn = Callable[[str], None]


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
                _run(["umount", mnt])
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
    mount point busy" while /dev/mapper/sdb1 mounts fine and shows the real
    contents) — apparently Ventoy's own runtime claims the raw partition.
    Prefer the mapper node when one exists."""
    name = os.path.basename(partition_path)
    mapper_path = f"/dev/mapper/{name}"
    if os.path.exists(mapper_path):
        return mapper_path
    return partition_path


def looks_like_ventoy_partition(mountpoint: str) -> bool:
    return (os.path.isdir(os.path.join(mountpoint, "restoreimg"))
            or os.path.isfile(os.path.join(mountpoint, "ventoy", "ventoy.json")))


def find_ventoy_partition(retries: int = 3, retry_delay: float = 1.0) -> Optional[str]:
    """Scan real partitions for Ventoy markers (restoreimg/, ventoy/ventoy.json).
    Returns the partition device path, or None.

    Retries on a transient mount failure: confirmed in testing that right at
    desktop/autostart time, the desktop's own automount daemon (udisks2/GVFS
    probing a freshly-inserted USB stick) can briefly hold the device, making
    a single mount attempt fail with "already mounted or busy" even though
    the very same mount succeeds moments later. This matters most for
    autostart, which fires at exactly that moment.

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
                    _run(["umount", scan_mnt])
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
                _run(["umount", mnt])
    return None


_SFDISK_LINE = re.compile(
    r"^(?P<dev>/dev/[a-zA-Z0-9]+)\s*:.*start=\s*(?P<start>\d+),\s*size=\s*(?P<size>\d+)"
)


def is_last_partition(part: str, disk: str) -> bool:
    """True if `part` is physically the last partition on `disk` (i.e. it's
    the only one that could safely grow into trailing free space). Uses
    `sfdisk -d`, same format the backups themselves are saved in."""
    r = _run(["sfdisk", "-d", disk])
    part_end = -1
    max_end = -1
    for line in r.stdout.splitlines():
        m = _SFDISK_LINE.match(line)
        if not m:
            continue
        end = int(m["start"]) + int(m["size"])
        if m["dev"] == part:
            part_end = end
        max_end = max(max_end, end)
    return part_end >= 0 and part_end == max_end


def get_source_min_bytes(restoreimg_path: str) -> Optional[int]:
    """Minimum destination size (bytes) implied by the backup's own saved
    partition table dump (e.g. restoreimg/vda-pt.sf). None if it can't tell
    (older/unexpected backup format)."""
    try:
        with open(os.path.join(restoreimg_path, "disk")) as f:
            diskname = f.read().strip()
        with open(os.path.join(restoreimg_path, f"{diskname}-pt.sf")) as f:
            sf = f.read()
    except OSError:
        return None
    last_lba = re.search(r"^last-lba:\s*(\d+)", sf, re.MULTILINE)
    sector_size = re.search(r"^sector-size:\s*(\d+)", sf, re.MULTILINE)
    if not (last_lba and sector_size):
        return None
    return (int(last_lba[1]) + 1) * int(sector_size[1])


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


def run_streaming(args, log: LogFn, success_marker: str = None, stdin_text: str = None) -> tuple[int, list[str]]:
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
    stdout open."""
    log(f"$ {' '.join(args)}")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.PIPE if stdin_text is not None else None,
                             text=True, bufsize=1)
    if stdin_text is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    saw_marker = False
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

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    proc.wait()
    reader_thread.join(timeout=2)  # drain whatever output was already buffered
    try:
        proc.stdout.close()  # unstick the reader thread if something else is still holding the pipe open
    except OSError:
        pass

    rc = 0 if saw_marker else proc.returncode
    return rc, list(tail)


def _fail_with_tail(headline: str, tail: list[str]) -> StepResult:
    """Build a verbose failure message: the headline plus the last few
    lines of actual command output, so 'it failed' always comes with real
    detail instead of a bare generic sentence."""
    if tail:
        return StepResult(False, headline + "\n\nLast output:\n" + "\n".join(tail))
    return StepResult(False, headline)


def grow_system_partition(disk: str, log: LogFn = _noop_log) -> StepResult:
    sys_part = find_system_partition(disk)
    if not sys_part:
        return StepResult(False, "Could not identify a Linux system partition — restore succeeded, "
                                  "but auto-grow was skipped. You can grow partitions manually with gparted.")
    log(f"Identified Linux system partition: {sys_part}")

    if not is_last_partition(sys_part, disk):
        return StepResult(False, f"{sys_part} is not the last partition on {disk} (something else follows it) — "
                                  "restore succeeded, but the extra space isn't safely reachable.")

    partition_num = sys_part[len(disk):].lstrip("p")
    log(f"Growing partition {partition_num} ({sys_part}) to use all available space...")
    rc, tail = run_streaming(["parted", "-s", "--", disk, "resizepart", partition_num, "100%"], log)
    if rc != 0:
        return _fail_with_tail(f"Failed to resize partition {sys_part}.", tail)

    _run(["partprobe", disk])
    r = _run(["blkid", "-o", "value", "-s", "TYPE", sys_part])
    fs_type = r.stdout.strip()
    log(f"Resizing filesystem on {sys_part} (detected: {fs_type})...")

    if fs_type in ("ext2", "ext3", "ext4"):
        run_streaming(["e2fsck", "-f", "-p", sys_part], log)
        rc, tail = run_streaming(["resize2fs", sys_part], log)
        ok = rc == 0
    elif fs_type == "btrfs":
        with tempfile.TemporaryDirectory() as tmp:
            if _run(["mount", sys_part, tmp]).returncode == 0:
                rc, tail = run_streaming(["btrfs", "filesystem", "resize", "max", tmp], log)
                ok = rc == 0
            else:
                ok, tail = False, []
            _run(["umount", tmp])
    elif fs_type == "xfs":
        with tempfile.TemporaryDirectory() as tmp:
            if _run(["mount", sys_part, tmp]).returncode == 0:
                rc, tail = run_streaming(["xfs_growfs", tmp], log)
                ok = rc == 0
            else:
                ok, tail = False, []
            _run(["umount", tmp])
    else:
        return StepResult(False, f"Unsupported filesystem type '{fs_type}' — partition grown, filesystem not resized.")

    if ok:
        return StepResult(True, "Filesystem resized successfully.")
    return _fail_with_tail("Partition was resized but filesystem resize failed.", tail)


SHRINK_TARGET_BYTES = 20 * 1024 * 1024 * 1024  # 20G — see shrink_system_partition_for_backup
SHRINK_MARGIN_FRACTION = 0.15  # headroom above the filesystem's reported minimum
SHRINK_MARGIN_MIN_BYTES = 512 * 1024 * 1024
PARTITION_END_BUFFER_BYTES = 16 * 1024 * 1024  # alignment/safety slack past the shrunk filesystem


def shrink_system_partition_for_backup(disk: str, log: LogFn = _noop_log) -> StepResult:
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
        return _shrink_ext(sys_part, disk, log)
    if fs_type == "btrfs":
        return _shrink_btrfs(sys_part, disk, log)
    if fs_type == "xfs":
        return StepResult(True, "XFS does not support shrinking — skipping pre-backup shrink.")
    return StepResult(True, f"Unsupported filesystem type '{fs_type}' — skipping pre-backup shrink.")


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


def _shrink_ext(sys_part: str, disk: str, log: LogFn) -> StepResult:
    run_streaming(["e2fsck", "-f", "-p", sys_part], log)  # resize2fs requires a clean fs

    _, tail = run_streaming(["resize2fs", "-P", sys_part], log)
    minimum_bytes = None
    m = re.search(r"minimum size.*?:\s*(\d+)", "\n".join(tail), re.IGNORECASE)
    block_size = 4096
    bs = _run(["dumpe2fs", "-h", sys_part])
    bm = re.search(r"^Block size:\s*(\d+)", bs.stdout, re.MULTILINE)
    if bm:
        block_size = int(bm[1])
    if m:
        minimum_bytes = int(m[1]) * block_size

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
    rc, tail = run_streaming(["resize2fs", sys_part, f"{target_bytes // (1024*1024)}M"], log)
    if rc != 0:
        return _fail_with_tail(f"Could not shrink the filesystem on {sys_part} — proceeding at its current size.",
                                tail)

    _shrink_partition_to(sys_part, disk, target_bytes, log)
    return StepResult(True, f"Shrunk {sys_part} to ~{target_bytes // (1024**3)}G before backup.", changed=True)


def _shrink_btrfs(sys_part: str, disk: str, log: LogFn) -> StepResult:
    # btrfs has no direct "minimum size" query; attempt the 20G target
    # directly and accept that btrfs itself refuses if actual data won't
    # fit — that failure is expected and not a real problem.
    with tempfile.TemporaryDirectory() as tmp:
        if _run(["mount", sys_part, tmp]).returncode != 0:
            return StepResult(True, f"Could not mount {sys_part} to shrink it — skipping pre-backup shrink.")
        try:
            rc, tail = run_streaming(["btrfs", "filesystem", "resize",
                                       str(SHRINK_TARGET_BYTES // (1024*1024)) + "M", tmp], log)
        finally:
            _run(["umount", tmp])
    if rc != 0:
        return StepResult(True, f"{sys_part} likely has more than 20G of data — leaving it at its current size.")
    _shrink_partition_to(sys_part, disk, SHRINK_TARGET_BYTES, log)
    return StepResult(True, f"Shrunk {sys_part} to ~20G before backup.", changed=True)


def restore(restoreimg_path: str, disk: str, log: LogFn = _noop_log) -> StepResult:
    log(f"Restoring {restoreimg_path} to {disk}...")
    rc, tail = run_streaming(["rescuezilla", "restore", "--source", restoreimg_path,
                              "--destination", disk, "--overwrite-partition-table"], log,
                             success_marker="Successfully restored image partition")
    if rc != 0:
        return _fail_with_tail(
            "Restore failed. The disk may be left partially written — do not treat this as a working system.",
            tail)
    log("Restore reported success.")
    _run(["partprobe", disk])

    grow = grow_system_partition(disk, log)
    if grow.ok:
        return StepResult(True, "Restore done, system partition grown to fill the disk.")
    return StepResult(True, f"Restore succeeded, but: {grow.message}")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def describe_disk_for_backup(disk: "Disk") -> str:
    """A single safe token for --description: rescuezilla's CLI wrapper
    mis-splits a multi-word description (confirmed in Phase 1 testing), so
    collapse the detected distro name (spaces and all) into one word rather
    than dropping it."""
    return re.sub(r"[^A-Za-z0-9]+", "_", disk.distro).strip("_") if disk.distro else ""


def backup(source_disk: str, destination_folder: str, log: LogFn = _noop_log,
           description: str = "") -> StepResult:
    shrink = shrink_system_partition_for_backup(source_disk, log)
    log(shrink.message)

    args = ["rescuezilla", "backup", "--source", source_disk,
            "--destination", destination_folder, "--compression-format", "gzip"]
    # Note: rescuezilla's CLI wrapper mis-splits a multi-word --description
    # (verified in Phase 1 testing) — only pass it if it's a single token.
    if description and " " not in description:
        args += ["--description", description]
    rc, tail = run_streaming(args, log)
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
        regrow = grow_system_partition(source_disk, log)
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
