**🇬🇧 English** | [🇮🇹 Italiano](build-guide.it.md)

# Build Guide

This guide covers *building* the ISO — fetching the latest upstream Rescuezilla release and baking in the PNLUG customizations (GUI, TUI, autostart, branding). If you just want to use an already-built stick, see the [Usage Guide](usage-guide.md) instead.

## Contents

- [Requirements](#requirements)
- [Running the build](#running-the-build)
- [What the build actually does](#what-the-build-actually-does)
- [Output](#output)
- [Modifying the customizations](#modifying-the-customizations)
- [Known quirks](#known-quirks)
- [Updating for a future Rescuezilla release](#updating-for-a-future-rescuezilla-release)
- [CI/CD: automatic builds on new Rescuezilla releases](#cicd-automatic-builds-on-new-rescuezilla-releases)

## Requirements

Any Linux machine with internet access and one of `apt`, `dnf`, or `pacman` (Debian/Ubuntu, Fedora, or Arch-family). That's it — the build script installs [Podman](https://podman.io/) if it isn't already present (nothing else touches the host), and everything else runs inside a pinned Ubuntu container, so the *result* is identical no matter which of those three the host actually is.

If Podman is already installed, no `sudo`/root is even needed on the host beyond whatever Podman itself requires for rootless containers (the default on all three distros above).

## Running the build

```bash
git clone https://github.com/LucaCraft89/pnlug-automated_rescuezilla.git
cd pnlug-automated_rescuezilla
build/build.sh [codename]
```

`codename` picks which upstream Rescuezilla ISO variant to base the build on — it corresponds to the Ubuntu release each Rescuezilla ISO is built against (check the asset names on the [upstream releases page](https://github.com/rescuezilla/rescuezilla/releases/latest), e.g. `rescuezilla-2.6.2-64bit.noble.iso`). Defaults to `noble` (Ubuntu 24.04 LTS) if omitted — the safest, longest-supported choice.

A full build takes roughly 10–20 minutes depending on your internet connection and CPU (most of that is downloading the ~1.5GB upstream ISO and re-compressing the squashfs, which is CPU-heavy).

## What the build actually does

(See `build/remaster.sh` for the exact, commented implementation — this is the short version.)

1. **Fetch** the latest matching-codename ISO from the [upstream Rescuezilla GitHub releases](https://github.com/rescuezilla/rescuezilla/releases/latest).
2. **Extract** the ISO filesystem with `xorriso`.
3. **Unpack** its squashfs (the actual live-Linux root filesystem) with `unsquashfs`.
4. **Apply PNLUG customizations**: copy in the GUI/TUI/shared-library scripts, the PNLUG logo, the `.desktop` launchers, and — importantly — hook the autostart script into `/etc/xdg/openbox/autostart` (this release's live session runs plain Openbox, which doesn't read the standard `/etc/xdg/autostart/*.desktop` directory at all; see [How It's Done](how-its-done.md) for the story behind that).
5. **Enable RAM-boot by default**: Rescuezilla's stock `grub.cfg` already ships a "Load USB into RAM" menu entry (boots with the casper `toram` kernel parameter) but defaults to the plain entry instead — the build script switches the default to the RAM-loading one and adds `noeject` alongside `toram`, so the boot medium isn't detached once copied to RAM (which would otherwise get in the way of the tool then mounting the Ventoy data partition to find `restoreimg/`). This means the whole live system runs from RAM after boot, and the stick itself is only touched again when actually reading/writing a backup.
6. **Repack** the squashfs with `mksquashfs` (the slow, CPU-bound step).
7. **Rebuild the ISO** with `xorriso`'s "boot image replay" feature, which reuses the *original* ISO's hybrid BIOS+UEFI boot structures byte-for-byte instead of us having to hand-roll isohybrid offsets or El Torito boot catalogs ourselves — only the two files that actually changed (the squashfs and its size sidecar) get swapped in.
8. **Copy out** the finished ISO plus the ready-to-copy `ventoy/` folder.

## Output

```
build/output/pnlug_zilla.iso   ← the built ISO, copy to the root of a Ventoy stick
build/output/ventoy/           ← theme + ventoy.json, copy into the stick's own ventoy/ folder
```

See the [Usage Guide](usage-guide.md#1-making-the-ventoy-stick) for exactly how to lay these out on a stick.

## Modifying the customizations

Everything that ends up on the ISO's live desktop lives in `src/`:

| File | What it is |
|---|---|
| `pnlug_rescue_lib.py` | Shared backend: disk discovery/safety, backup, restore, partition growing. **Single source of truth** — the GUI and TUI both call into this, so a fix here fixes both. |
| `pnlug_gui_common.py` | Shared GTK widgets (header, confirmation dialog, progress+log page) used by both GUI apps. |
| `pnlug-rescue-gui` | The restore GUI. |
| `pnlug-backup-gui` | The backup GUI. |
| `pnlug-rescue-tui` | The curses text-menu version of both flows. |
| `pnlug-autostart.sh` | Runs at desktop load: GUI → TUI → visible error, in that order. |
| `desktop/*.desktop` | Desktop icons and the (mostly-unused, see above) freedesktop autostart entry. |
| `pnlugrescue.sh` | The original bash-only implementation, kept on the ISO for manual/CLI use and backward compatibility. |

Edit any of these, then re-run `build/build.sh` — the next build picks up your changes automatically (nothing needs to be "installed" separately; the build script just copies the current contents of `src/` into the squashfs each time).

## Known quirks

- **`unsquashfs` prints `create_inode: failed to create character device ... Operation not permitted` for a handful of `/dev/*` entries.** This is expected and harmless: rootless containers can never create real device nodes (not even with the `MKNOD` capability added — that specific kernel restriction applies regardless), and the live system's own boot process replaces `/dev` with a fresh `devtmpfs` at boot anyway, so the few static placeholders missing from the squashfs are never actually used. The build script explicitly tolerates `unsquashfs`'s exit code 2 (its "some files not extracted" code) for exactly this reason — a *different* nonzero exit code still fails the build.
- **`mksquashfs` is the slow step** — expect several minutes of near-100%-CPU compression regardless of network speed. This is normal.
- **The build downloads a fresh ~1.5GB ISO every single run** — there's no cross-run cache (each build uses a throwaway container). If you're iterating on `src/` changes locally, budget for that download time each time, or work directly against an already-extracted squashfs for fast iteration and only run the full `build.sh` for the final artifact.

## Updating for a future Rescuezilla release

The build always fetches the *latest* release automatically — no version to bump by hand. The one thing that could break a future run: if Rescuezilla changes where the squashfs lives inside the ISO (currently `casper/filesystem.squashfs`). The script already searches for it dynamically (`find "$ISOTREE" -name "*.squashfs"`) rather than hardcoding the path, so a rename alone won't break it — only a genuinely different live-boot mechanism (e.g. moving away from Casper entirely) would need a real script update at that point.

## CI/CD: automatic builds on new Rescuezilla releases

`.github/workflows/build-and-release.yml` in this repo does exactly what it sounds like: on a schedule (and on-demand via the Actions tab's "Run workflow" button), it checks the latest upstream Rescuezilla release tag, and if this repo hasn't already published a release for that tag, it runs the exact same `build/build.sh` on a GitHub-hosted runner and publishes the result as a new GitHub Release here, with the ISO and the `ventoy/` folder attached.

This was genuinely not much extra work on top of `build.sh` itself — GitHub's `ubuntu-latest` runners already have enough disk and CPU for the build (a bit slower than a typical dev machine, so budget ~20–25 minutes per run), and the only real additions were: installing Podman on the runner (one `apt-get` line), a short step to compare the upstream tag against this repo's existing release tags (skip and exit cleanly if nothing new), and a release-publish step using [`gh release create`](https://cli.github.com/manual/gh_release_create). See the workflow file itself for the exact steps — it's under 60 lines including comments.

To trigger it by hand instead of waiting for the schedule: go to this repo's **Actions** tab → **Build and Release** workflow → **Run workflow**.
