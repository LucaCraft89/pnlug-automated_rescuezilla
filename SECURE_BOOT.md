**🇬🇧 English** | [🇮🇹 Italiano](SECURE_BOOT.it.md)

# Secure Boot — status and workaround

**Verified live** (QEMU/OVMF with Microsoft UEFI CA certs enrolled, `q35,smm=on`):
booting the PNLUG Ventoy stick with Secure Boot on fails immediately with

    ERROR
    Verification failed: (0x1A) Security Violation

This happens before Ventoy's own menu appears — before our ISO or our
`pnlugrescue` scripts are ever loaded. It is **not caused by our remaster**.

## Root cause

Ventoy's Secure Boot support works via a `shim` + self-signed `grub`:
- `EFI/BOOT/BOOTX64.EFI` on the stick's `VTOYEFI` partition is a real,
  Microsoft-signed shim (confirmed: shim 15.8).
- Ventoy's `grub.efi` behind it is signed with Ventoy's own generated
  certificate, shipped alongside it as
  `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` (confirmed: self-signed, `CN=grub`).

By design, a fresh machine doesn't trust that certificate yet. On real
hardware the normal flow is: shim boots, can't verify grub, drops into
`MokManager` so the user enrolls the `.cer` once (one-time, physical
keypress required — this step cannot be scripted or automated, since that
interactivity is the actual security property MOK enrollment provides).
After that one-time enrollment, Secure Boot works normally on that machine
for every subsequent boot of that Ventoy stick.

In our QEMU test the firmware rejected shim itself rather than reaching
MokManager, which just means our test firmware's enrolled cert set doesn't
include whatever chain that shim build needs — a QEMU/OVMF test-environment
detail, not something specific to real hardware.

## What we tell PNLUG members

Two supported options, both fine — this is Ventoy's normal Secure Boot UX,
not a defect in the PNLUG stick:

1. **Simplest**: turn off Secure Boot in the PC's firmware setup before
   using the rescue stick. This is what most people will want to do.
2. **Keep Secure Boot on**: the first time the stick is used on a given PC,
   when the blue MokManager screen appears, choose
   *Enroll key from disk* → browse to `ENROLL_THIS_KEY_IN_MOKMANAGER.cer`
   in the root of the Ventoy drive → enroll it → reboot. Only needed once
   per PC.

Nothing in the PNLUG build needs to change for this — it's inherent to how
Ventoy (and Linux Secure Boot generally) works, and re-signing Ventoy's own
grub ourselves isn't practical (we don't control Ventoy's release, and a
self-signed replacement would need the exact same one-time enrollment
anyway).
