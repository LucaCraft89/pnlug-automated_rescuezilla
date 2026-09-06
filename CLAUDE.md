# CLAUDE.md

Guidance for Claude Code (or any AI agent) working in this repository. Read this before touching anything — it explains what this project is, how the pieces fit together, and a list of real bugs already found and fixed so they don't get re-introduced or re-discovered from scratch.

## What this is

A customized, self-building [Rescuezilla](https://github.com/rescuezilla/rescuezilla) live ISO + [Ventoy](https://www.ventoy.net/) USB layout, built for **PNLUG APS** (Pordenone Linux User Group). Purpose: deploy the *same pre-configured OS image* onto many PCs from *one* USB stick, unattended, safely, with no Linux knowledge required from whoever runs it.

Human-facing docs (start here for the "what"/"why" — this file is for the "how to work on the code"):
- [`README.md`](README.md) — overview, EN/IT
- [`docs/usage-guide.md`](docs/usage-guide.md) — how to build the stick and use it
- [`docs/build-guide.md`](docs/build-guide.md) — how the build script works
- [`docs/how-its-done.md`](docs/how-its-done.md) — full backstory, architecture rationale, and a narrative version of the bug list below
- [`SECURE_BOOT.md`](SECURE_BOOT.md) — Secure Boot behavior, not a defect

Every doc above exists in English (primary) and Italian (`*.it.md`), each with a language-switcher link at the very top. **If you touch the English version of a doc, update the Italian one too** (or at minimum flag that it's now stale) — don't let them drift silently.

## Repo layout

```
src/            Everything that ends up on the ISO's live desktop.
                Copied into the squashfs verbatim by build/remaster.sh — never
                edit the ISO/squashfs directly, always edit here and rebuild.
build/          build.sh (host entry point, installs Podman) and remaster.sh
                (the actual remaster logic, runs inside a pinned Ubuntu
                container). build/output/ is git-ignored (build artifact).
ventoy/         PNLUG Ventoy theme + ventoy.json — copied onto a Ventoy
                stick's data partition by end users, also copied into
                build/output/ventoy/ by the build for convenience.
docs/           User-facing guides (see above).
.github/workflows/build-and-release.yml   CI: watches upstream Rescuezilla,
                auto-builds + publishes a GitHub Release on a new tag.
```

## Architecture: one backend, two front-ends

`src/pnlug_rescue_lib.py` is the **single source of truth** for every piece of real logic: disk discovery/exclusion, size safety checks, partition growing/shrinking, backup, restore. Both front-ends — the GTK GUI (`pnlug-rescue-gui`, `pnlug-backup-gui`, sharing widgets from `pnlug_gui_common.py`) and the curses TUI (`pnlug-rescue-tui`) — call into this same module and never reimplement any of its logic themselves. **A fix belongs in `pnlug_rescue_lib.py`, not duplicated into both front-ends.**

Key conventions in that module:
- `StepResult(ok: bool, message: str, changed: bool = False)` — the return type for every operation. `changed` is `True` only when something on disk was actually modified (currently used by `shrink_system_partition_for_backup` so `backup()` knows whether it needs to grow the source back afterward — a no-op shrink like "already small enough" must never trigger a regrow, since nothing was touched).
- `LogFn = Callable[[str], None]` — every long-running function takes a `log` callback instead of printing directly, so the GUI can stream it into a text view and the TUI into a curses pane with the same calls.
- `run_streaming(args, log, success_marker=None, stdin_text=None)` — the one place that shells out to external commands with live log streaming. Reads on a background thread and waits on `proc.wait()`, not pipe EOF (see the Known Gotchas list — pipe EOF can hang forever here). `success_marker` exists because the stock `rescuezilla` wrapper has its own exit-code bug (see below) — pass the real success string to look for instead of trusting the process exit code.
- `resolve_mountable(partition_path)` — always call this before mounting a Ventoy/system partition. Real Ventoy sticks wrap partitions in device-mapper nodes on a full desktop boot; the raw `/dev/sdX1` path is often "busy."
- `find_system_partition(disk)` identifies the Linux root **by content** (`/etc/fstab` + `/usr/bin` or `/bin`), never by size or position. Don't regress this to a size-based heuristic.

`pnlugrescue.sh` is the original bash-only implementation, kept on the ISO for CLI/manual use and backward compatibility. It is **not** the thing to edit for behavior changes — `pnlug_rescue_lib.py` is authoritative for the GUI/TUI, which is what real users actually use.

## Autostart chain

`src/pnlug-autostart.sh`: GUI → TUI (in a terminal) → visible error dialog, in that order. A normal close of the GUI (user finishes or just closes the window) is success, not a failure — only "couldn't even start" triggers the fallback.

**Do not assume this release's live session uses `xfce4-session` or `gnome-session`.** It runs plain `openbox-session`, which never reads `/etc/xdg/autostart/*.desktop` at all (confirmed live — the freedesktop autostart entry silently never fired). The real entry point is a line `build/remaster.sh` appends directly into `/etc/xdg/openbox/autostart`. The `.desktop` entry is kept too, purely in case a future Rescuezilla release switches session managers — don't rely on it alone, and don't remove the openbox hook.

## Build system

`build/build.sh [codename]` → installs Podman if missing → runs `build/remaster.sh` inside a pinned `ubuntu:24.04` container. `remaster.sh` fetches the latest matching upstream Rescuezilla ISO, extracts it, unpacks/patches/repacks the squashfs, patches `boot/grub/grub.cfg`, and rebuilds the ISO with `xorriso`'s boot-image-replay feature. Read `docs/build-guide.md` for the step-by-step; read the script itself (it's short and heavily commented) before changing it.

CI (`.github/workflows/build-and-release.yml`) runs the same `build.sh` on a schedule, skips if a release for the current upstream tag already exists, and publishes a GitHub Release (`rescuezilla-<upstream-tag>`) with `pnlug_zilla.iso` + `ventoy.zip` attached when it's genuinely new.

## Known gotchas — read before "fixing" these again

These were each found through real, live QEMU/OVMF testing, not guessed. If you see the symptom again, this is almost certainly why:

1. **`unsquashfs` prints `create_inode: failed to create character device ... Operation not permitted`** for a handful of `/dev/*` entries, and exits 2. This is expected in rootless containers (no capability grants real `mknod` for device nodes) and harmless (`devtmpfs` replaces `/dev` at boot anyway). `remaster.sh` explicitly tolerates exit code 2 specifically — do not "fix" this by chasing full root/privileged containers; a *different* nonzero exit code should still fail the build.
2. **`parted -s resizepart` to a *smaller* size cannot be scripted** — its "Shrinking a partition can cause data loss" confirmation is not bypassable via stdin in script mode, tried multiple ways. Use `sfdisk -N <partnum> --no-reread <disk>` with `", <newsize>M\n"` on stdin instead (see `_shrink_partition_to`). Growing (`resizepart ... 100%`) works fine with plain `parted -s`.
3. **`xorriso`'s boot-image-replay breaks if you `-map` the whole extracted tree.** It needs the original ISO's own boot-catalog-referenced files (El Torito images, GRUB2 MBR patch) to resolve by reference to the *original* image, not fresh local copies at the same paths. Only `-map` the specific files you actually changed (squashfs, its size sidecar, `grub.cfg`) — see `remaster.sh`'s comments at the final `xorriso` call.
4. **The stock `rescuezilla` CLI wrapper has its own exit-code bug**: after a genuinely successful restore/backup, its cleanup path calls `systemctl --user --runtime unmask --quiet --` with no unit names, which `systemctl` rejects, and that becomes the wrapper's own (wrong) exit code. Don't trust the wrapper's exit code alone — `restore()` checks for the real "Successfully restored image partition" success marker; `backup()` checks that the actual output file exists and is non-empty.
5. **`run_streaming` must wait on `proc.wait()`, not pipe EOF.** Some unrelated long-lived process the wrapper touches (suspected `systemd-udevd`) can inherit the pipe's write end and hold it open even after the real child has exited, so `for line in proc.stdout` can hang forever. Read on a background thread, wait on the process directly, then force-close the pipe.
6. **A GTK bug can silently freeze the UI at the last log line with zero error output**: an exception thrown inside a `GLib.idle_add` callback is swallowed by GLib, not raised anywhere visible. If a progress page appears to hang after real backend work has clearly finished (check `/proc/<pid>/task/*/wchan` — if every thread is idle, nothing is actually blocked), suspect a bug in the idle callback itself before suspecting the subprocess layer.
7. **A log file `rm -f`'d and recreated, not truncated in place.** `pnlug-autostart.sh`'s log removes-then-creates rather than truncating, because a stale file from a *different* user context (the live session's normal user vs. a root maintenance shell) can be un-truncatable by the other but is always unlinkable (only needs directory permission). A failed redirect target stops the whole redirected command from running, not just the logging — this silently broke the entire GUI→TUI→error chain before the fix.
8. **`toram` alone isn't enough for a Ventoy stick — pair it with `noeject`.** Casper's default RAM-boot behavior can interfere with mounting *other* partitions on the same physical stick afterward (like the one holding `restoreimg/`). `noeject` stops it from trying to detach the boot medium once copied to RAM. `remaster.sh` sets both together when it switches the default grub entry.
9. **Backup's pre-shrink must always be undone afterward, but only if it actually happened.** `shrink_system_partition_for_backup`'s `StepResult.changed` flag exists specifically so `backup()` can tell a real shrink apart from a no-op — growing back an already-correctly-sized disk would be a *new* bug in the other direction.

## Testing methodology

There's no automated test suite — every change above was verified against a real, disposable QEMU/OVMF VM: a genuine Ventoy USB image, disk images of differing sizes, a real installed Linux distro backed up and restored across them, and deliberately-broken scenarios (missing binaries, wrong permissions, undersized destinations, Secure Boot on) to confirm safety checks and fallbacks actually behave as designed. This rig isn't checked into the repo (it's disposable, multi-GB test images), but the shape of it is:

- OVMF UEFI firmware (`edk2-ovmf` on Fedora/RHEL, `ovmf` on Debian/Ubuntu)
- A real Ventoy-installed USB image (`qemu-img create -f raw ventoy.img <size>`, then run Ventoy's own installer against it via a loop device, or against a real stick and `dd` it off)
- One or more blank/pre-installed disk images of different sizes to exercise backup/restore/grow across genuinely different disk geometries
- QEMU's QMP socket (`-qmp unix:qmp.sock,server,nowait`) for scripted screendumps and input injection — useful for driving the GTK GUI through a full flow non-interactively
- A `-virtfs` 9p share for pushing updated files into a running guest without rebuilding the ISO each iteration

When testing a `pnlug_rescue_lib.py`-only change, you don't need a full ISO rebuild: copy the updated file into a running guest via the 9p share (or however you're getting files in) and re-run against it directly — much faster than the ~15–20 minute full build cycle. Only rebuild the ISO to verify a `remaster.sh`/packaging-level change, or right before cutting a release.

## Licensing note

Rescuezilla is GPLv3. This project only ever *adds* separate files alongside Rescuezilla's own (desktop entries, our own scripts, branding) — it never modifies Rescuezilla's own source or binaries. That's "mere aggregation" under GPLv3 §5, which is why the PNLUG-authored code here can carry its own MIT license (see `LICENSE`) without pulling the whole ISO under GPL. If you ever start patching Rescuezilla's *own* files (not just adding new ones next to them), re-check this reasoning before shipping it.
