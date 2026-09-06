[🇬🇧 English](build-guide.md) | **🇮🇹 Italiano**

# Guida alla compilazione

Questa guida copre la *compilazione* della ISO — scaricare l'ultima versione di Rescuezilla e integrarci le personalizzazioni PNLUG (GUI, TUI, avvio automatico, marchio). Se vuoi solo usare una chiavetta già compilata, vedi invece la [Guida all'uso](usage-guide.it.md).

## Indice

- [Requisiti](#requisiti)
- [Eseguire la compilazione](#eseguire-la-compilazione)
- [Cosa fa davvero la compilazione](#cosa-fa-davvero-la-compilazione)
- [Output](#output)
- [Modificare le personalizzazioni](#modificare-le-personalizzazioni)
- [Particolarità note](#particolarita-note)
- [Aggiornare per una futura versione di Rescuezilla](#aggiornare-per-una-futura-versione-di-rescuezilla)
- [CI/CD: compilazioni automatiche alle nuove versioni di Rescuezilla](#cicd-compilazioni-automatiche-alle-nuove-versioni-di-rescuezilla)

## Requisiti

Qualsiasi macchina Linux con accesso a internet e uno tra `apt`, `dnf` o `pacman` (famiglia Debian/Ubuntu, Fedora, o Arch). Tutto qui — lo script di build installa [Podman](https://podman.io/) se non è già presente (nient'altro tocca l'host), e tutto il resto viene eseguito dentro un container Ubuntu fissato, quindi il *risultato* è identico indipendentemente da quale dei tre l'host effettivamente sia.

Se Podman è già installato, non serve nemmeno `sudo`/root sull'host, oltre a quanto Podman stesso richiede per i container rootless (il default su tutte e tre le distribuzioni sopra citate).

## Eseguire la compilazione

```bash
git clone https://github.com/LucaCraft89/pnlug-automated_rescuezilla.git
cd pnlug-automated_rescuezilla
build/build.sh [codename]
```

`codename` sceglie quale variante della ISO Rescuezilla a monte usare come base — corrisponde alla versione di Ubuntu su cui è costruita ogni ISO di Rescuezilla (controlla i nomi degli asset nella [pagina delle release a monte](https://github.com/rescuezilla/rescuezilla/releases/latest), es. `rescuezilla-2.6.2-64bit.noble.iso`). Se omesso, di default è `noble` (Ubuntu 24.04 LTS) — la scelta più sicura e supportata più a lungo.

Una compilazione completa richiede circa 10–20 minuti a seconda della connessione internet e della CPU (la maggior parte del tempo va nello scaricare la ISO a monte da ~1.5GB e nel ricomprimere lo squashfs, operazione pesante per la CPU).

## Cosa fa davvero la compilazione

(Vedi `build/remaster.sh` per l'implementazione esatta e commentata — questa è la versione breve.)

1. **Scarica** l'ultima ISO con codename corrispondente dalle [release GitHub di Rescuezilla a monte](https://github.com/rescuezilla/rescuezilla/releases/latest).
2. **Estrae** il filesystem della ISO con `xorriso`.
3. **Decomprime** il suo squashfs (il vero filesystem radice del Linux live) con `unsquashfs`.
4. **Applica le personalizzazioni PNLUG**: copia gli script di GUI/TUI/libreria condivisa, il logo PNLUG, i lanciatori `.desktop` e — cosa importante — collega lo script di avvio automatico a `/etc/xdg/openbox/autostart` (la sessione live di questa release usa Openbox puro, che non legge affatto la cartella standard `/etc/xdg/autostart/*.desktop`; vedi [Come è stato fatto](how-its-done.it.md) per la storia dietro a questo).
5. **Attiva l'avvio in RAM di default**: il `grub.cfg` di serie di Rescuezilla include già una voce di menu "Load USB into RAM" (avvia con il parametro del kernel casper `toram`) ma di default usa invece la voce normale — lo script di build cambia il default su quella voce con avvio in RAM e aggiunge `noeject` insieme a `toram`, così il supporto di avvio non viene scollegato una volta copiato in RAM (cosa che altrimenti ostacolerebbe il montaggio successivo della partizione dati Ventoy per trovare `restoreimg/`). Questo significa che l'intero sistema live gira dalla RAM dopo l'avvio, e la chiavetta stessa viene toccata di nuovo solo quando si legge/scrive davvero un backup.
6. **Ricomprime** lo squashfs con `mksquashfs` (il passaggio lento, limitato dalla CPU).
7. **Ricostruisce la ISO** con la funzione "boot image replay" di `xorriso`, che riutilizza byte per byte le strutture di avvio ibride BIOS+UEFI della ISO *originale* invece di dover ricostruire a mano gli offset isohybrid o i cataloghi di avvio El Torito — vengono sostituiti solo i due file effettivamente cambiati (lo squashfs e il suo file di dimensione associato).
8. **Copia** la ISO finita più la cartella `ventoy/` pronta da copiare.

## Output

```
build/output/pnlug_zilla.iso   ← la ISO compilata, copiare nella radice di una chiavetta Ventoy
build/output/ventoy/           ← tema + ventoy.json, copiare nella cartella ventoy/ della chiavetta
```

Vedi la [Guida all'uso](usage-guide.it.md#1-preparare-la-chiavetta-ventoy) per come disporre esattamente questi file su una chiavetta.

## Modificare le personalizzazioni

Tutto ciò che finisce sul desktop live della ISO si trova in `src/`:

| File | Cosa è |
|---|---|
| `pnlug_rescue_lib.py` | Backend condiviso: individuazione/sicurezza dei dischi, backup, ripristino, ingrandimento delle partizioni. **Unica fonte di verità** — sia la GUI che la TUI chiamano questo codice, quindi una correzione qui corregge entrambe. |
| `pnlug_gui_common.py` | Widget GTK condivisi (intestazione, finestra di conferma, pagina di progresso+log) usati da entrambe le app GUI. |
| `pnlug-rescue-gui` | La GUI di ripristino. |
| `pnlug-backup-gui` | La GUI di backup. |
| `pnlug-rescue-tui` | La versione a menu testuale (curses) di entrambi i flussi. |
| `pnlug-autostart.sh` | Viene eseguito all'avvio del desktop: GUI → TUI → errore visibile, in quest'ordine. |
| `desktop/*.desktop` | Icone del desktop e la voce di avvio automatico freedesktop (per lo più inutilizzata, vedi sopra). |
| `pnlugrescue.sh` | L'implementazione originale, solo bash, mantenuta sulla ISO per uso manuale/da riga di comando e retrocompatibilità. |

Modifica uno qualsiasi di questi file, poi riesegui `build/build.sh` — la compilazione successiva recepisce automaticamente le modifiche (non serve "installare" nulla separatamente; lo script di build copia semplicemente il contenuto attuale di `src/` nello squashfs ogni volta).

## Particolarità note

- **`unsquashfs` stampa `create_inode: failed to create character device ... Operation not permitted` per alcune voci `/dev/*`.** È previsto e innocuo: i container non privilegiati non possono mai creare veri nodi di dispositivo (nemmeno aggiungendo la capacità `MKNOD` — quella specifica restrizione del kernel si applica comunque), e il processo di avvio del sistema live sostituisce comunque `/dev` con un `devtmpfs` fresco all'avvio, quindi i pochi segnaposto statici mancanti dallo squashfs non vengono mai realmente usati. Lo script di build tollera esplicitamente il codice di uscita 2 di `unsquashfs` (il suo codice per "alcuni file non estratti") proprio per questo motivo — un codice di uscita diverso da zero *diverso* fa comunque fallire la compilazione.
- **`mksquashfs` è il passaggio lento** — aspettati diversi minuti di compressione quasi al 100% di CPU indipendentemente dalla velocità di rete. È normale.
- **La compilazione scarica una ISO fresca da ~1.5GB ogni singola volta** — non c'è cache tra le esecuzioni (ogni compilazione usa un container usa e getta). Se stai iterando su modifiche a `src/` in locale, considera questo tempo di download ogni volta, oppure lavora direttamente su uno squashfs già estratto per un'iterazione rapida ed esegui `build.sh` completo solo per l'artefatto finale.

## Aggiornare per una futura versione di Rescuezilla

La compilazione recupera sempre automaticamente l'ultima release — nessuna versione da aggiornare a mano. L'unica cosa che potrebbe rompere un'esecuzione futura: se Rescuezilla cambia dove si trova lo squashfs dentro la ISO (attualmente `casper/filesystem.squashfs`). Lo script già lo cerca dinamicamente (`find "$ISOTREE" -name "*.squashfs"`) invece di codificare il percorso, quindi una semplice rinomina non lo romperà — solo un meccanismo di avvio live davvero diverso (per esempio l'abbandono completo di Casper) richiederebbe a quel punto un vero aggiornamento dello script.

## CI/CD: compilazioni automatiche alle nuove versioni di Rescuezilla

`.github/workflows/build-and-release.yml` in questo repository fa esattamente quello che sembra: secondo una pianificazione (e su richiesta tramite il pulsante "Run workflow" nella scheda Actions), controlla l'ultimo tag di release di Rescuezilla a monte, e se questo repository non ha ancora pubblicato una release per quel tag, esegue lo stesso identico `build/build.sh` su un runner ospitato da GitHub e pubblica il risultato come nuova GitHub Release qui, con la ISO e la cartella `ventoy/` allegate.

Non è stato davvero molto lavoro in più oltre a `build.sh` stesso — i runner `ubuntu-latest` di GitHub hanno già disco e CPU sufficienti per la compilazione (un po' più lenti di una tipica macchina di sviluppo, quindi considera ~20–25 minuti per esecuzione), e le uniche aggiunte reali sono state: installare Podman sul runner (una riga di `apt-get`), un breve passaggio per confrontare il tag a monte con i tag di release già esistenti in questo repository (salta ed esce senza errori se non c'è nulla di nuovo), e un passaggio di pubblicazione della release con [`gh release create`](https://cli.github.com/manual/gh_release_create). Vedi il file del workflow stesso per i passaggi esatti — sono meno di 60 righe, commenti inclusi.

Per attivarlo manualmente invece di aspettare la pianificazione: vai nella scheda **Actions** di questo repository → workflow **Build and Release** → **Run workflow**.
