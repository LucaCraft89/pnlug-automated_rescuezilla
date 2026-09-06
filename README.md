**🇬🇧 English** | [🇮🇹 Italiano](README.it.md)

# PNLUG Automated Rescuezilla

A customized, self-building [Rescuezilla](https://github.com/rescuezilla/rescuezilla) live ISO plus a [Ventoy](https://www.ventoy.net/) USB layout, built by and for **[PNLUG APS](https://www.pnlug.it)** (Pordenone Linux User Group, Associazione di Promozione Sociale) to deploy the **same pre-configured operating system image onto many PCs from a single USB flash drive**, unattended and safely, without requiring the person running the drive to know Linux.

Plug the stick into a machine, and the tool takes over: it finds the backup on the stick, picks the right internal disk, double-checks with the user, restores the image, and grows the filesystem to fill whatever disk it landed on. The same stick also makes new backups just as easily, so preparing the next "golden image" is a few clicks, not a manual `dd`/`partclone` session.

## Documentation

All guides exist in English (primary) and Italian — every document starts with a language switcher like the one above.

| Guide | Description |
|---|---|
| [**Usage Guide**](docs/usage-guide.md) ([it](docs/usage-guide.it.md)) | How to build the Ventoy stick, make a backup, restore it onto target PCs, and a full troubleshooting/FAQ section. **Start here if you just want to use the stick.** |
| [**Build Guide**](docs/build-guide.md) ([it](docs/build-guide.it.md)) | How to (re)build the ISO from the latest upstream Rescuezilla release using the included Podman-based build script, and how the CI/CD auto-release works. |
| [**How It's Done**](docs/how-its-done.md) ([it](docs/how-its-done.it.md)) | The story and architecture behind this project — why it exists, the design decisions, and the bugs found and fixed along the way. |
| [**Secure Boot notes**](SECURE_BOOT.md) ([it](SECURE_BOOT.it.md)) | Why Secure Boot shows an error on first use, and the one-time fix. |

## What's in this repo

```
src/            The GUI (GTK3), TUI (curses), shared safety/backup/restore logic,
                autostart script, and .desktop launchers — this is what gets baked
                into the ISO.
build/          build.sh (host entry point) and remaster.sh (the actual remaster,
                runs in a pinned container via Podman) — see the Build Guide.
ventoy/         The PNLUG Ventoy theme and ventoy.json config (Italian keyboard
                default, PNLUG branding, 3-second auto-boot into the ISO) — copy
                this onto a Ventoy stick's data partition, see the Usage Guide.
docs/           The guides linked above.
.github/        The CI/CD workflow that watches for new upstream Rescuezilla
                releases and auto-builds/publishes a new PNLUG release.
```

## Quick start

- **Just want to use an existing PNLUG rescue stick?** → [Usage Guide](docs/usage-guide.md)
- **Want to build your own stick from scratch, or rebuild after a Rescuezilla update?** → [Build Guide](docs/build-guide.md)
- **Curious how/why this was built?** → [How It's Done](docs/how-its-done.md)

## About PNLUG

This project was built for and by **[PNLUG APS](https://www.pnlug.it)** — Pordenone Linux User Group, Associazione di Promozione Sociale — a nonprofit association promoting Linux and free software in and around Pordenone, Italy. Community knowledge base and further documentation: **[wiki.pnlug.it](https://wiki.pnlug.it)**.

## License

Released under the [MIT License](LICENSE). Rescuezilla and Ventoy are separate upstream projects with their own licenses — see their respective repositories.
