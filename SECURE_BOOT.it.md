[🇬🇧 English](SECURE_BOOT.md) | **🇮🇹 Italiano**

# Secure Boot — stato e soluzione

**Verificato dal vivo** (QEMU/OVMF con i certificati Microsoft UEFI CA registrati, `q35,smm=on`):
avviare la chiavetta Ventoy PNLUG con il Secure Boot attivo fallisce subito con

    ERROR
    Verification failed: (0x1A) Security Violation

Questo accade prima ancora che appaia il menu di Ventoy — prima che vengano caricati la nostra ISO o i nostri script `pnlugrescue`. **Non è causato dal nostro remaster.**

## Causa

Il supporto Secure Boot di Ventoy funziona tramite uno `shim` + un `grub` autofirmato:
- `EFI/BOOT/BOOTX64.EFI` sulla partizione `VTOYEFI` della chiavetta è uno shim reale, firmato da Microsoft (confermato: shim 15.8).
- Il `grub.efi` di Ventoy dietro di esso è firmato con un certificato generato da Ventoy stesso, distribuito insieme ad esso come `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` (confermato: autofirmato, `CN=grub`).

Per progettazione, un PC "vergine" non si fida ancora di quel certificato. Su hardware reale il flusso normale è: lo shim si avvia, non riesce a verificare grub, ed entra in `MokManager` così l'utente registra il `.cer` una sola volta (operazione manuale, richiede una pressione fisica di tasto — questo passaggio non può essere scriptato o automatizzato, perché è proprio quell'interattività a garantire la proprietà di sicurezza della registrazione MOK). Dopo quella registrazione una tantum, il Secure Boot funziona normalmente su quel PC per ogni avvio successivo della stessa chiavetta Ventoy.

Nel nostro test QEMU il firmware ha rifiutato direttamente lo shim invece di arrivare a MokManager, il che significa solo che l'insieme di certificati registrati nel nostro firmware di test non include la catena di cui quella build di shim ha bisogno — un dettaglio dell'ambiente di test QEMU/OVMF, non qualcosa di specifico dell'hardware reale.

## Cosa diciamo ai soci PNLUG

Due opzioni supportate, entrambe valide — questo è il normale comportamento del Secure Boot di Ventoy, non un difetto della chiavetta PNLUG:

1. **Più semplice**: disattivare il Secure Boot nel firmware del PC prima di usare la chiavetta di ripristino. È quello che la maggior parte delle persone vorrà fare.
2. **Mantenere il Secure Boot attivo**: la prima volta che la chiavetta viene usata su un dato PC, quando appare la schermata blu di MokManager, scegliere *Enroll key from disk* → cercare `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` nella radice della chiavetta Ventoy → registrarlo → riavviare. Serve una sola volta per ogni PC.

Non serve modificare nulla nella build PNLUG per questo — è una caratteristica intrinseca di come funzionano Ventoy (e il Secure Boot di Linux in generale), e rifirmare noi stessi il grub di Ventoy non è praticabile (non controlliamo la release di Ventoy, e una sostituzione autofirmata richiederebbe comunque la stessa registrazione una tantum).
