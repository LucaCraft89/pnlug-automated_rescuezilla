[🇬🇧 English](README.md) | **🇮🇹 Italiano**

# PNLUG Automated Rescuezilla

Una ISO live di [Rescuezilla](https://github.com/rescuezilla/rescuezilla) personalizzata e autocostruita, insieme a una configurazione di chiavetta USB [Ventoy](https://www.ventoy.net/), realizzata da e per il **[PNLUG APS](https://www.pnlug.it)** (Pordenone Linux User Group, Associazione di Promozione Sociale) per installare **lo stesso sistema operativo già configurato su più PC partendo da un'unica chiavetta USB**, in modo automatico e sicuro, senza che chi la utilizza debba conoscere Linux.

Basta collegare la chiavetta a un PC: il programma fa tutto da solo — trova il backup sulla chiavetta, individua il disco interno giusto, chiede conferma all'utente, ripristina l'immagine e ridimensiona il filesystem per riempire il disco su cui è finito. La stessa chiavetta permette anche di creare nuovi backup con la stessa facilità, quindi preparare la prossima "immagine campione" richiede pochi click, non una sessione manuale con `dd`/`partclone`.

## Documentazione

Tutte le guide esistono in inglese (principale) e in italiano — ogni documento inizia con un selettore di lingua come quello qui sopra.

| Guida | Descrizione |
|---|---|
| [**Guida all'uso**](docs/usage-guide.it.md) ([en](docs/usage-guide.md)) | Come costruire la chiavetta Ventoy, creare un backup, ripristinarlo sui PC di destinazione, e una sezione completa di risoluzione problemi/FAQ. **Inizia da qui se vuoi solo usare la chiavetta.** |
| [**Guida alla compilazione**](docs/build-guide.it.md) ([en](docs/build-guide.md)) | Come (ri)costruire la ISO dall'ultima versione di Rescuezilla usando lo script di build basato su Podman incluso, e come funziona il rilascio automatico via CI/CD. |
| [**Come è stato fatto**](docs/how-its-done.it.md) ([en](docs/how-its-done.md)) | La storia e l'architettura dietro questo progetto — perché esiste, le scelte progettuali, e i bug trovati e risolti lungo il percorso. |
| [**Note sul Secure Boot**](SECURE_BOOT.it.md) ([en](SECURE_BOOT.md)) | Perché il Secure Boot mostra un errore al primo utilizzo, e la soluzione (una tantum). |

## Cosa contiene questo repository

```
src/            La GUI (GTK3), la TUI (curses), la logica condivisa di sicurezza/
                backup/ripristino, lo script di autostart e i lanciatori .desktop —
                è quello che finisce dentro la ISO.
build/          build.sh (punto d'ingresso sull'host) e remaster.sh (il vero e
                proprio remaster, eseguito in un container fissato tramite
                Podman) — vedi la Guida alla compilazione.
ventoy/         Il tema Ventoy PNLUG e la configurazione ventoy.json (layout
                tastiera italiano di default, branding PNLUG, avvio automatico
                dopo 3 secondi nella ISO) — copiare nella cartella ventoy/ di
                una chiavetta Ventoy, vedi la Guida all'uso.
docs/           Le guide linkate sopra.
.github/        Il workflow CI/CD che controlla nuove versioni di Rescuezilla
                a monte e compila/pubblica automaticamente una nuova release
                PNLUG.
```

## Avvio rapido

- **Vuoi solo usare una chiavetta di ripristino PNLUG già pronta?** → [Guida all'uso](docs/usage-guide.it.md)
- **Vuoi costruire la tua chiavetta da zero, o ricompilare dopo un aggiornamento di Rescuezilla?** → [Guida alla compilazione](docs/build-guide.it.md)
- **Curioso di sapere come/perché è stato realizzato?** → [Come è stato fatto](docs/how-its-done.it.md)

## Il PNLUG

Questo progetto è stato realizzato per e da **[PNLUG APS](https://www.pnlug.it)** — Pordenone Linux User Group, Associazione di Promozione Sociale — un'associazione senza scopo di lucro che promuove Linux e il software libero a Pordenone e dintorni. Base di conoscenza della comunità e documentazione aggiuntiva: **[wiki.pnlug.it](https://wiki.pnlug.it)**.

## Licenza

Rilasciato con licenza [MIT](LICENSE). Rescuezilla e Ventoy sono progetti a monte separati con licenze proprie — vedere i rispettivi repository.
