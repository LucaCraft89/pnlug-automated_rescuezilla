**🇬🇧 English** | [🇮🇹 Italiano](how-its-done.it.md)

# How It's Done

The story behind this project: why it exists, the decisions behind how it's built, and some of the more interesting bugs found and fixed along the way. If you just want to *use* the stick or *build* the ISO, see the [Usage Guide](usage-guide.md) or [Build Guide](build-guide.md) instead — this page is background, not instructions.

## The actual problem

**[PNLUG APS](https://www.pnlug.it)** regularly needs to put the same, already-configured operating system onto more than one PC — club machines, loaner laptops, member setups, workshop stations. Doing that by hand, PC by PC, means either reinstalling the OS and redoing every bit of configuration each time, or manually cloning a disk with `dd`/`partclone` and then fixing up partition sizes, bootloaders, and anything hardware-specific afterward — every single time, and none of it forgiving of a mistake (wipe the wrong disk once and there's no undo).

The goal from the start was: **set a PC up exactly right once, make one backup, and from then on any PC can be turned into a copy of it by someone who doesn't know Linux, just by plugging in a USB stick and confirming a couple of on-screen prompts.**

## Why Rescuezilla + Ventoy

- **[Rescuezilla](https://github.com/rescuezilla/rescuezilla)** is a mature, actively-maintained Clonezilla-based backup/restore tool with its own live ISO — it already solves the hard, easy-to-get-wrong parts (partition-table-aware disk imaging, filesystem-aware compression, live-boot environment) reliably. No reason to reinvent that.
- **[Ventoy](https://www.ventoy.net/)** turns a single USB stick into a boot menu for any number of ISOs, and — critically for this project — lets you keep arbitrary *other* files (like the actual backup image) on the same exFAT data partition, right alongside the ISO. One stick carries both the tool and the payload.

Together: one stick, no re-flashing between uses, no separate "backup drive."

## What was missing, and what got built

Rescuezilla on its own is a general-purpose recovery tool aimed at someone who already knows what a partition table is. Turning it into something a PNLUG member can hand to someone else and say "just boot this and follow the prompts" needed:

1. **A guided, safe front-end.** A GTK GUI (with a curses text-menu fallback for when there's no working display) that auto-detects the backup, recommends the right disk, and makes it structurally impossible to accidentally wipe the Ventoy stick itself — the stick's own disk is excluded from every disk list before the user ever sees it, not just discouraged.
2. **Automatic partition growing.** A backup taken from a small disk needs to expand to fill whatever bigger disk it lands on next, automatically, including correctly identifying *which* partition is actually the Linux root (by content — presence of `/etc/fstab` and `/usr/bin` or `/bin` — not by guessing from size or position).
3. **An unattended flow.** The right tool should just *appear* when the live desktop loads — no memorizing which icon to double-click.
4. **A repeatable build.** The ISO needed to be rebuildable from a fresh upstream Rescuezilla release on demand, not a one-off hand-edited image nobody could reproduce or update later.

### One shared library, not two implementations

The GUI and the TUI are separate front-ends, but they share exactly one backend module (`src/pnlug_rescue_lib.py`) for every piece of actual logic: which disks are candidates, which one to recommend, how to identify the system partition, how to grow it, how to shrink it before a backup, how to check a destination is big enough before wiping it. Neither front-end reimplements any of that — they just call into the same functions and render the results differently. A safety fix made once applies to both automatically; there's no way for the GUI and TUI to quietly drift into different, inconsistent behavior.

### Safety by construction, not by warning

A few examples of choices made specifically so a mistake isn't possible rather than just "warned against":
- The Ventoy stick's own disk is resolved and removed from the candidate list in code, before any UI renders it — it's not there to accidentally click.
- Restoring onto a disk smaller than the backup is refused before anything is wiped, not caught partway through.
- Every failure shows the *actual* last lines of the underlying tool's output, not a generic "something went wrong" — because a generic error is useless for actually fixing anything, and hiding detail "to keep it simple" just moves the confusion to a worse moment.

### Booting straight into RAM

A live ISO that stays mounted from the USB stick for its entire session works, but it means every disk read for the tool's own UI competes with USB read speed, and — more importantly for this project — it means the boot medium is "in use" in a way that can complicate mounting *other* partitions on the same stick (like the one holding `restoreimg/`). Casper (Ubuntu's live-boot system) already supports a `toram` kernel parameter that copies the whole compressed filesystem into RAM early in boot and runs from there afterward; Rescuezilla's own stock `grub.cfg` even ships a menu entry for it — just not as the default. The build script switches the default to that entry and adds the complementary `noeject` parameter (which stops casper trying to detach the boot medium once the RAM copy is done) — the fix here wasn't inventing new behavior, just turning on what was already built into both Casper and Rescuezilla's own menu, and pairing it with the one extra flag needed for a Ventoy stick's particular "boot medium and payload storage are the same device" arrangement.

### The autostart bug that took real investigation

Getting the right tool to launch automatically turned out to be less obvious than "install a `.desktop` file in `/etc/xdg/autostart/`" — because that directory is a *freedesktop* convention, and it's only honored by session managers that implement it (like `xfce4-session` or `gnome-session`). Live testing showed the `.desktop` autostart entry simply never fired, with no error anywhere. Digging into `lightdm`'s own debug log revealed the actual live session runs plain `openbox-session` — a minimal window manager with no autostart-spec support at all; it only ever runs whatever's explicitly listed in its own `/etc/xdg/openbox/autostart` shell script. The real fix was appending the launch line straight into that file during the build, alongside (not instead of) the standard `.desktop` entry in case a future Rescuezilla release switches session managers.

A second, subtler bug turned up testing the fallback chain itself: the autostart log file was being *truncated in place* on each run, which works fine until the file happens to already be owned by a different user than the one now running the script (which can genuinely happen between a live session's normal user and a root maintenance shell) — at that point every redirect into the log silently failed, which stopped the redirected command from running at all, breaking the entire GUI→TUI→error chain rather than just the logging. The fix was to delete-then-recreate the log file instead of truncating it, since deleting only needs permission on the containing directory, not on the file itself.

## The build: reproducible, not hand-edited

The ISO is never edited by hand — `build/build.sh` + `build/remaster.sh` do it from scratch every time, starting from whatever the *current* upstream Rescuezilla release actually is:

- **Podman, in a pinned container**, so the build produces an identical result whether the person running it is on Debian, Fedora, or Arch — the host only ever needs Podman itself installed; every actual build tool (`xorriso`, `squashfs-tools`) lives inside the container and never touches the host.
- **`xorriso`'s "boot image replay"** reuses the original ISO's own hybrid BIOS+UEFI boot structures directly, rather than reconstructing isohybrid offsets and El Torito boot catalogs by hand — a real, previously-untried technique that turned out to have a sharp edge: replaying the boot image *while also* remapping the entire extracted file tree onto the new ISO breaks it, because the boot catalog's own references expect to resolve against the *original* image's data objects, not freshly-added copies of the same paths. The fix was mapping in only the two files that actually changed (the squashfs and its size sidecar) and leaving everything else referenced straight from the original ISO.
- **CI/CD** (see the [Build Guide](build-guide.md#cicd-automatic-builds-on-new-rescuezilla-releases)) runs the same build script on a schedule, checks whether upstream has published a release this repo hasn't built yet, and publishes a new GitHub Release automatically when it has — so a new Rescuezilla release doesn't require anyone to remember to go rebuild anything by hand.

## Testing methodology

Every change described above was verified against a real, disposable QEMU/OVMF virtual machine — real Ventoy USB image, real disk images of differing sizes, a real installed Linux distribution backed up and restored across them, and deliberately broken scenarios (missing binaries, wrong permissions, undersized destination disks, Secure Boot enabled) to confirm the safety checks and fallbacks actually behave as designed rather than just looking right in the source.

## Credits

Built for and by **[PNLUG APS](https://www.pnlug.it)** — Pordenone Linux User Group, Associazione di Promozione Sociale. Community knowledge base: **[wiki.pnlug.it](https://wiki.pnlug.it)**. Built on top of the excellent, independently-licensed [Rescuezilla](https://github.com/rescuezilla/rescuezilla) and [Ventoy](https://www.ventoy.net/) projects — see their own repositories for their licenses and to support their maintainers directly.
