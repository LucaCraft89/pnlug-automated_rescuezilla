[🇬🇧 English](how-its-done.md) | **🇮🇹 Italiano**

# Come è stato fatto

La storia dietro questo progetto: perché esiste, le decisioni dietro come è costruito, e alcuni dei bug più interessanti trovati e risolti lungo il percorso. Se vuoi solo *usare* la chiavetta o *compilare* la ISO, vedi invece la [Guida all'uso](usage-guide.it.md) o la [Guida alla compilazione](build-guide.it.md) — questa pagina è contesto, non istruzioni.

## Il problema vero

Il **[PNLUG APS](https://www.pnlug.it)** ha regolarmente bisogno di installare lo stesso sistema operativo già configurato su più di un PC — macchine del circolo, portatili in prestito, postazioni dei soci, stazioni per i workshop. Farlo a mano, PC per PC, significa o reinstallare il sistema operativo e rifare ogni singola configurazione ogni volta, oppure clonare manualmente un disco con `dd`/`partclone` e poi sistemare a posteriori dimensioni delle partizioni, bootloader e qualsiasi cosa specifica dell'hardware — ogni singola volta, e senza margine di errore (cancellare per sbaglio il disco sbagliato una volta e non c'è modo di tornare indietro).

L'obiettivo fin dall'inizio è stato: **configurare un PC esattamente come si vuole una sola volta, creare un backup, e da quel momento in poi qualsiasi PC può diventarne una copia da parte di chi non conosce Linux, semplicemente collegando una chiavetta USB e confermando un paio di richieste a schermo.**

## Perché Rescuezilla + Ventoy

- **[Rescuezilla](https://github.com/rescuezilla/rescuezilla)** è uno strumento di backup/ripristino maturo e attivamente mantenuto, basato su Clonezilla, con una propria ISO live — risolve già in modo affidabile le parti difficili e facili da sbagliare (imaging dei dischi consapevole delle tabelle delle partizioni, compressione consapevole del filesystem, ambiente di avvio live). Nessun motivo per reinventarlo.
- **[Ventoy](https://www.ventoy.net/)** trasforma un'unica chiavetta USB in un menu di avvio per un numero qualsiasi di ISO, e — cosa fondamentale per questo progetto — permette di tenere altri file arbitrari (come l'immagine di backup vera e propria) sulla stessa partizione dati exFAT, proprio accanto alla ISO. Un'unica chiavetta porta sia lo strumento che il contenuto.

Insieme: un'unica chiavetta, nessuna riscrittura tra un uso e l'altro, nessuna "chiavetta di backup" separata.

## Cosa mancava, e cosa è stato costruito

Rescuezilla da solo è uno strumento di ripristino generico pensato per chi sa già cos'è una tabella delle partizioni. Trasformarlo in qualcosa che un socio PNLUG possa consegnare a qualcun altro dicendo "avvia questo e segui le istruzioni" richiedeva:

1. **Un'interfaccia guidata e sicura.** Una GUI GTK (con un menu testuale curses di riserva per quando non c'è un display funzionante) che individua automaticamente il backup, consiglia il disco giusto, e rende strutturalmente impossibile cancellare per errore la chiavetta Ventoy stessa — il disco della chiavetta viene escluso da ogni elenco di dischi prima ancora che l'utente lo veda, non solo sconsigliato.
2. **Ingrandimento automatico delle partizioni.** Un backup preso da un disco piccolo deve espandersi per riempire qualsiasi disco più grande su cui finisca dopo, automaticamente, incluso identificare correttamente *quale* partizione sia davvero la radice Linux (dal contenuto — presenza di `/etc/fstab` e `/usr/bin` o `/bin` — non indovinando dalla dimensione o dalla posizione).
3. **Un flusso automatico.** Lo strumento giusto dovrebbe semplicemente *apparire* quando si carica il desktop live — nessuna icona da ricordare su cui fare doppio click.
4. **Una compilazione ripetibile.** La ISO doveva poter essere ricostruita a richiesta da una versione fresca di Rescuezilla a monte, non un'immagine modificata a mano una tantum che nessuno potrebbe più riprodurre o aggiornare.

### Un'unica libreria condivisa, non due implementazioni

La GUI e la TUI sono interfacce separate, ma condividono esattamente un modulo di backend (`src/pnlug_rescue_lib.py`) per ogni singola parte di logica reale: quali dischi sono candidati, quale consigliare, come identificare la partizione di sistema, come ingrandirla, come restringerla prima di un backup, come controllare che una destinazione sia abbastanza grande prima di cancellarla. Nessuna delle due interfacce reimplementa nulla di tutto ciò — chiamano semplicemente le stesse funzioni e ne mostrano i risultati in modo diverso. Una correzione di sicurezza fatta una volta si applica automaticamente a entrambe; non c'è modo che GUI e TUI finiscano per divergere silenziosamente in comportamenti diversi e inconsistenti.

### Sicurezza per costruzione, non per avviso

Alcuni esempi di scelte fatte apposta perché un errore non sia possibile, invece di essere solo "sconsigliato":
- Il disco della chiavetta Ventoy viene individuato e rimosso dall'elenco dei candidati nel codice, prima che qualsiasi interfaccia lo mostri — non è lì per essere cliccato per sbaglio.
- Ripristinare su un disco più piccolo del backup viene rifiutato prima che qualcosa venga cancellato, non intercettato a metà strada.
- Ogni errore mostra le *vere* ultime righe di output dello strumento sottostante, non un generico "qualcosa è andato storto" — perché un errore generico è inutile per risolvere davvero qualcosa, e nascondere i dettagli "per semplicità" sposta solo la confusione a un momento peggiore.

### Il bug dell'avvio automatico che ha richiesto una vera indagine

Far partire automaticamente lo strumento giusto si è rivelato meno ovvio di "installare un file `.desktop` in `/etc/xdg/autostart/`" — perché quella cartella è una convenzione *freedesktop*, ed è rispettata solo dai gestori di sessione che la implementano (come `xfce4-session` o `gnome-session`). I test dal vivo hanno mostrato che la voce di avvio automatico `.desktop` semplicemente non partiva mai, senza alcun errore visibile. Scavando nel log di debug di `lightdm` è emerso che la sessione live reale usa `openbox-session` puro — un window manager minimale senza alcun supporto per la specifica di avvio automatico; esegue solo ciò che è esplicitamente elencato nel proprio script `/etc/xdg/openbox/autostart`. La vera correzione è stata aggiungere la riga di avvio direttamente a quel file durante la compilazione, insieme (non al posto) alla normale voce `.desktop`, nel caso una futura release di Rescuezilla cambiasse gestore di sessione.

Un secondo bug, più sottile, è emerso testando proprio la catena di fallback: il file di log dell'avvio automatico veniva *troncato sul posto* a ogni esecuzione, il che funziona bene finché il file non risulta già di proprietà di un utente diverso da quello che sta eseguendo lo script in quel momento (cosa che può davvero succedere tra l'utente normale di una sessione live e una shell di manutenzione da root) — a quel punto ogni redirezione verso il log falliva silenziosamente, il che impediva l'esecuzione stessa del comando redirezionato, rompendo l'intera catena GUI→TUI→errore invece del solo logging. La correzione è stata cancellare e ricreare il file di log invece di troncarlo, dato che cancellare richiede solo il permesso sulla cartella che lo contiene, non sul file stesso.

### Avvio diretto in RAM

Una ISO live che resta montata dalla chiavetta USB per l'intera sessione funziona, ma significa che ogni lettura su disco per l'interfaccia dello strumento stesso compete con la velocità della USB, e — cosa più importante per questo progetto — significa che il supporto di avvio è "in uso" in un modo che può complicare il montaggio di *altre* partizioni sulla stessa chiavetta (come quella che contiene `restoreimg/`). Casper (il sistema di avvio live di Ubuntu) supporta già un parametro del kernel `toram` che copia l'intero filesystem compresso in RAM all'inizio dell'avvio e da lì in poi gira da quello; il `grub.cfg` di serie di Rescuezilla include perfino una voce di menu per questo — solo non come predefinita. Lo script di build cambia il default su quella voce e aggiunge il parametro complementare `noeject` (che impedisce a casper di provare a scollegare il supporto di avvio una volta completata la copia in RAM) — la correzione qui non è stata inventare un nuovo comportamento, ma solo attivare ciò che era già integrato sia in Casper che nel menu di Rescuezilla stesso, abbinandolo all'unico parametro extra necessario per la particolare configurazione di una chiavetta Ventoy in cui "supporto di avvio" e "archiviazione del contenuto" sono lo stesso dispositivo.

## La compilazione: riproducibile, non modificata a mano

La ISO non viene mai modificata a mano — `build/build.sh` + `build/remaster.sh` la ricostruiscono da zero ogni volta, partendo da qualunque sia *attualmente* l'ultima release di Rescuezilla a monte:

- **Podman, in un container fissato**, così la compilazione produce un risultato identico sia che chi la esegue usi Debian, Fedora o Arch — l'host deve avere installato solo Podman stesso; ogni vero strumento di compilazione (`xorriso`, `squashfs-tools`) vive dentro il container e non tocca mai l'host.
- **Il "boot image replay" di `xorriso`** riutilizza direttamente le strutture di avvio ibride BIOS+UEFI della ISO originale, invece di ricostruire a mano gli offset isohybrid e i cataloghi di avvio El Torito — una tecnica reale, mai provata prima in questo contesto, che si è rivelata avere un lato tagliente: riprodurre l'immagine di avvio mentre *allo stesso tempo* si rimappa l'intero albero di file estratto sulla nuova ISO la rompe, perché i riferimenti del catalogo di avvio si aspettano di risolvere rispetto agli oggetti dati dell'immagine *originale*, non a copie appena aggiunte degli stessi percorsi. La correzione è stata mappare solo i due file effettivamente cambiati (lo squashfs e il suo file di dimensione associato) e lasciare tutto il resto riferito direttamente dalla ISO originale.
- **CI/CD** (vedi la [Guida alla compilazione](build-guide.it.md#cicd-compilazioni-automatiche-alle-nuove-versioni-di-rescuezilla)) esegue lo stesso script di build secondo una pianificazione, controlla se a monte è stata pubblicata una release che questo repository non ha ancora compilato, e pubblica automaticamente una nuova GitHub Release quando è così — così una nuova release di Rescuezilla non richiede che qualcuno si ricordi di andare a ricompilare qualcosa a mano.

## Metodologia di test

Ogni modifica descritta sopra è stata verificata su una macchina virtuale QEMU/OVMF reale e usa e getta — una vera immagine USB Ventoy, vere immagini disco di dimensioni diverse, una vera distribuzione Linux installata, salvata e ripristinata tra di esse, e scenari deliberatamente rotti (file binari mancanti, permessi sbagliati, dischi di destinazione troppo piccoli, Secure Boot attivo) per confermare che i controlli di sicurezza e i fallback si comportino davvero come progettato e non solo sembrino corretti nel codice sorgente.

## Crediti

Realizzato per e da **[PNLUG APS](https://www.pnlug.it)** — Pordenone Linux User Group, Associazione di Promozione Sociale. Base di conoscenza della comunità: **[wiki.pnlug.it](https://wiki.pnlug.it)**. Costruito sopra gli ottimi progetti, con licenze indipendenti, [Rescuezilla](https://github.com/rescuezilla/rescuezilla) e [Ventoy](https://www.ventoy.net/) — vedere i rispettivi repository per le loro licenze e per sostenere direttamente chi li mantiene.
