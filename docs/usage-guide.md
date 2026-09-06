**🇬🇧 English** | [🇮🇹 Italiano](usage-guide.it.md)

# Usage Guide

This guide covers everything about *using* the PNLUG rescue stick: preparing it, making a backup ("golden image"), restoring it onto target PCs, and troubleshooting. If you want to rebuild the ISO itself, see the [Build Guide](build-guide.md) instead.

## Contents

- [What this actually does](#what-this-actually-does)
- [1. Making the Ventoy stick](#1-making-the-ventoy-stick)
- [2. Making a backup (the "golden image")](#2-making-a-backup-the-golden-image)
- [3. Restoring onto a target PC](#3-restoring-onto-a-target-pc)
- [4. Using the text menu (TUI) instead of the GUI](#4-using-the-text-menu-tui-instead-of-the-gui)
- [5. Desktop shortcuts](#5-desktop-shortcuts)
- [6. Secure Boot](#6-secure-boot)
- [Troubleshooting / FAQ](#troubleshooting--faq)

## What this actually does

The point of this whole project: **install the exact same, already-configured operating system onto many different PCs from one USB stick**, without whoever is doing it needing to know Linux, partition tables, or Rescuezilla's own interface. Set up one PC exactly the way you want it once, make one backup, then plug the same stick into any number of other PCs and restore — each one ends up with the same OS, same apps, same settings, correctly resized to fill whatever disk it landed on.

Two apps do the actual work, always the same two regardless of whether you're using the mouse-driven GUI or the keyboard-driven text menu:

- **PNLUG Restore** — finds the backup on the stick, picks the right internal disk, double-checks with you, restores, and grows the filesystem to use the whole disk.
- **PNLUG Backup** — picks the disk to back up, shrinks its main partition down (to ~20GB, or as small as the actual data allows) so the backup is smaller and faster, then saves it to the stick.

Both are already on the stick's live desktop; you never install anything to use it. The live system also boots straight into RAM by default (the whole point of a Ventoy stick — the tool keeps working with `restoreimg/` on the stick's data partition, but the stick itself isn't needed to keep the OS running once it's up), so it's fine to leave the stick plugged in for the whole session without worrying about USB read speed slowing anything down after boot.

## 1. Making the Ventoy stick

You only need to do this once per stick (or again if you rebuild the ISO — see the [Build Guide](build-guide.md)).

1. Get any USB stick — 8GB is enough for the live system alone; add the size of your backup (a shrunk backup is usually well under 20GB) on top for `restoreimg/`. 32GB+ is a comfortable size.
2. Install [Ventoy](https://www.ventoy.net/en/download.html) onto the stick using Ventoy's own installer (`Ventoy2Disk.exe` on Windows, `VentoyWeb.sh`/`Ventoy2Disk.sh` on Linux). This formats the stick and creates two partitions: a big exFAT data partition (where you'll drop files) and a small hidden `VTOYEFI` partition Ventoy needs for booting. Follow [Ventoy's own installation guide](https://www.ventoy.net/en/doc_start.html) if this is your first time — it's a five-minute, one-time step and not specific to this project.
3. Grab the two files you need from the **[latest release](https://github.com/LucaCraft89/pnlug-automated_rescuezilla/releases/latest)** — you don't need to build anything or clone the repo just to use the stick:
   - **`pnlug_zilla.iso`**
   - **`ventoy.zip`** (the same `ventoy/` folder that's in this repo, zipped up)

   (Building it yourself instead? Use `build/output/pnlug_zilla.iso` and the `build/output/ventoy/` folder — see the [Build Guide](build-guide.md).)
4. Once Ventoy is installed, the stick shows up as a normal exFAT drive. Copy onto its root:
   - **`pnlug_zilla.iso`**, as-is.
   - **The contents of `ventoy.zip`** — extract it and copy the `ventoy/` folder it contains into the root of the stick, merging with the `ventoy/` folder Ventoy's own installer already created there (`ventoy.json`, `ventoy_backup.json`, and the `theme/` folder all end up inside it). This is what gives the boot menu the PNLUG branding and makes it auto-select `pnlug_zilla.iso` after 3 seconds if nobody touches the keyboard (see `ventoy.json`'s `VTOY_DEFAULT_IMAGE` and `VTOY_MENU_TIMEOUT` keys, and the [plugin docs](https://www.ventoy.net/en/plugin_control.html) if you want to tweak them) — Ventoy still shows one more "which boot mode" menu after that, which needs a single Enter press (see [step 2](#2-making-a-backup-the-golden-image) below). It also sets the default keyboard layout to Italian (`VTOY_DEFAULT_KBD_LAYOUT`) — change that key if your keyboards are laid out differently.

That's it — the stick is ready. Nothing else needs installing on it; `restoreimg/` (the backup) gets created automatically the first time you use PNLUG Backup.

## 2. Making a backup (the "golden image")

Do this on **one** reference PC, set up exactly the way you want every target machine to end up.

1. Plug the stick into the reference PC and boot from it (see your PC's boot-menu key — commonly `F12`, `F11`, `Esc`, or `F2`/`Del` for the BIOS setup where you can reorder boot devices). If Secure Boot is on, see [Secure Boot](#6-secure-boot) below.
2. Ventoy shows its menu with the PNLUG theme; either press Enter on the ISO or just wait 3 seconds for it to auto-select. It then shows one more menu asking "Boot in normal mode / grub2 mode / File checksum" — this one needs an actual Enter press (it's Ventoy's own per-image menu, not something the PNLUG config controls); **Boot in normal mode** is already highlighted, so a single Enter is enough.
3. The live desktop loads and the **PNLUG Auto Rescue** window opens automatically (this is actually the *restore* tool auto-starting — see [FAQ](#the-wrong-app-restore-not-backup-opened-automatically) if you specifically want backup to open instead). Close it, or open **PNLUG Backup** from the desktop icon.
4. PNLUG Backup lists your real disks (any Ventoy/live media is hidden for safety) and recommends the one that actually has your OS on it — pre-selected. Confirm it's the right disk and click **Continue**.
5. A confirmation dialog shows the disk's model, size, and current contents, and — if a backup already exists on the stick — warns that it will be replaced. Confirm.
6. It shrinks the system partition first (this can take a few minutes on a large disk — it's normal), then runs the actual backup, streaming full progress and log output the whole time. When it says **"Backup completed successfully"**, you're done.
7. The backup lives at `restoreimg/` on the stick's data partition. Copy that whole folder somewhere safe if you want a second copy — it's not tied to any one stick.

## 3. Restoring onto a target PC

1. Plug the same stick into the target PC and boot from it, same as above.
2. The live desktop loads and **PNLUG Auto Rescue** opens automatically. If it doesn't (see [Troubleshooting](#the-gui-never-opens-automatically)), open it from the desktop icon, or use the text menu (see [below](#4-using-the-text-menu-tui-instead-of-the-gui)).
3. It looks for `restoreimg/` on the stick automatically. If found, it skips straight to disk selection; if not, it opens a folder browser already pointed at the stick so you can navigate to it manually.
4. It lists the target PC's real disks (the Ventoy stick itself is always hidden here — you cannot accidentally select it) and recommends the largest fixed disk, pre-selected. **Read the confirmation dialog carefully** — it shows the disk's model, size, and current contents, and warns that everything on it will be permanently erased.
5. Confirm, and the restore runs: partition table, then each partition, then the system partition is grown to fill whatever space is left on the disk. Full log output stays visible the whole time — if something goes wrong, you'll see the real error, not just "it failed" (see [Troubleshooting](#restore-or-backup-says-it-failed---what-do-i-actually-check)).
6. When it finishes, it offers to reboot the PC immediately, or you can close the window and reboot later.

The restored machine boots into the exact same OS, apps, and settings as the reference PC — with its main partition automatically grown to use the whole new disk, however big or small it is (as long as it's not smaller than the original — see the size-check note in the FAQ).

## 4. Using the text menu (TUI) instead of the GUI

If there's no working display (or you just prefer a keyboard-only flow), the exact same restore/backup logic is available as a text menu: open **PNLUG Rescue (Terminal)** from the desktop, or it launches itself automatically if the graphical tool can't start (see [How It's Done](how-its-done.md) for why both exist). It asks the same questions, shows the same confirmations, and enforces the same safety checks — arrow keys to move, Enter to select.

## 5. Desktop shortcuts

The live desktop always has these icons, so you never have to remember a command:

| Icon | What it does |
|---|---|
| **PNLUG Restore** | Opens the restore GUI directly. |
| **PNLUG Backup** | Opens the backup GUI directly. |
| **PNLUG Rescue (Terminal)** | Opens the text-menu version (restore or backup, your choice, in one tool). |

## 6. Secure Boot

If the PC's firmware has Secure Boot enabled, you'll see a `Verification failed: (0x1A) Security Violation` error before anything else loads. This is expected, not specific to PNLUG — it's how Ventoy's own Secure Boot support normally works the first time on a new PC. Two options, both fine:

- **Simplest:** turn off Secure Boot in the PC's firmware setup, then boot the stick normally.
- **Keep Secure Boot on:** the blue MokManager screen that appears instead lets you choose *Enroll key from disk* → the `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` file at the root of the stick → enroll it → reboot. This is a one-time step per PC; every later boot of the stick on that same PC works normally afterward.

Full details in [Secure Boot notes](../SECURE_BOOT.md).

## Troubleshooting / FAQ

#### The stick doesn't show up in the boot menu at all
Check the PC's firmware boot-mode setting (UEFI vs. Legacy/CSM) — Ventoy supports both, but if the firmware is locked to one and the stick was written in the other mode it may not show up as a boot option. Also confirm the stick is actually plugged into a USB port the firmware scans at boot (some PCs only scan certain ports before the OS loads).

#### I get a Secure Boot / "Security Violation" error
See [Secure Boot](#6-secure-boot) above — this isn't a stick problem, it's expected first-boot behavior.

#### "Couldn't find restoreimg/ automatically" — it went to a folder browser
This means the tool couldn't find a Ventoy-marked partition with a `restoreimg/` folder on it. Most often this means:
- The backup hasn't been made yet on this stick (see [step 2](#2-making-a-backup-the-golden-image)).
- You're using a different stick than the one the backup was made on.
- The `restoreimg/` folder got moved, renamed, or deleted.

In the folder browser, just navigate to wherever your backup actually is and click **Use this folder** — everything else works the same from there.

#### The destination disk I want isn't in the list, or it's greyed out with a size warning
The tool refuses a destination smaller than the backed-up disk, to avoid a restore that silently fails partway through. If your target disk is genuinely smaller than the source PC's disk was, you'll need to either use a bigger disk, or make a smaller backup on the reference PC first (shrink its partitions manually before backing up, in addition to the automatic ~20GB shrink already applied).

#### Restore or backup says it "failed" — what do I actually check?
The tool is deliberately verbose on failure: the message includes the actual last lines of output from the underlying restore/backup engine, not just "it failed". Read that detail first — common causes are a disk that's failing/disconnected mid-operation, or running out of space on the destination (for backups, if the stick itself is nearly full). The full session log also stays on screen above the error the whole time; scroll up if you need more context.

#### The restored PC won't boot afterward
- Make sure the target PC's firmware boot mode (UEFI/Legacy) is set the same way the reference PC's was — a restored disk keeps whatever boot method it was originally installed with.
- If the reference PC used Secure Boot with its own enrolled keys, note that a restored disk's own bootloader signature doesn't change — Secure Boot behavior on the target PC follows the *target's* firmware settings, not the source's.
- The restore step deliberately does **not** reinstall the bootloader (it restores the disk's own boot sector/partition contents as backed up) — if the very first boot record was somehow specific to the original hardware, that's the first thing to check.

#### The wrong app (Restore, not Backup) opened automatically
The automatic one at desktop startup is always **Restore** — this matches the primary real-world use case (most boots of a finished stick are to deploy the golden image onto a new PC, not to remake it). To make a new backup, just close the auto-started restore window and open **PNLUG Backup** from the desktop icon instead — nothing else needs to change.

#### The GUI never opens automatically
It should launch itself a few seconds after the desktop appears. If it genuinely doesn't (and it isn't just still loading — the desktop icons appearing doesn't mean everything has finished starting), it means the graphical tool couldn't start at all — in that case it should have fallen back to the text menu in a terminal window instead, and finally to a plain error message with `/tmp/pnlug-gui-autostart.log` for details if even that failed. Check for that terminal or error window; if you don't see either, open **PNLUG Rescue (Terminal)** manually from the desktop — it does the exact same thing.

#### Can I run a backup and a restore from the same stick, back to back?
Yes — that's exactly the point. Nothing about using the stick for a backup changes anything about later using it for a restore, or vice versa, and there's no need to "reset" the stick between the two.

#### Where are the logs?
`/tmp/pnlug-gui-autostart.log` inside the live session covers the autostart chain (which tool tried to launch, and why a fallback happened, if one did). The GUI and TUI both keep the full operation log visible on screen for the entire run — nothing scrolls away or gets hidden, even on success.

#### Does this touch networking, Wi-Fi, or MAC addresses on the target PC?
No changes are made to networking configuration or MAC addresses. If your golden image included tools for that (e.g. a first-boot script that regenerates unique machine identifiers), those run as part of the restored OS's own normal boot process, not as part of this tool — restoring only writes back the disk contents and grows the partition.
