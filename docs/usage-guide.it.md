[🇬🇧 English](usage-guide.md) | **🇮🇹 Italiano**

# Guida all'uso

Questa guida copre tutto quello che riguarda l'*uso* della chiavetta di ripristino PNLUG: prepararla, creare un backup ("immagine campione"), ripristinarlo sui PC di destinazione, e la risoluzione dei problemi. Se vuoi ricostruire la ISO stessa, vedi invece la [Guida alla compilazione](build-guide.it.md).

## Indice

- [Cosa fa realmente](#cosa-fa-realmente)
- [1. Preparare la chiavetta Ventoy](#1-preparare-la-chiavetta-ventoy)
- [2. Creare un backup (l'"immagine campione")](#2-creare-un-backup-limmagine-campione)
- [3. Ripristinare su un PC di destinazione](#3-ripristinare-su-un-pc-di-destinazione)
- [4. Usare il menu testuale (TUI) invece della GUI](#4-usare-il-menu-testuale-tui-invece-della-gui)
- [5. Icone sul desktop](#5-icone-sul-desktop)
- [6. Secure Boot](#6-secure-boot)
- [Risoluzione problemi / FAQ](#risoluzione-problemi--faq)

## Cosa fa realmente

Lo scopo di tutto questo progetto: **installare esattamente lo stesso sistema operativo, già configurato, su tanti PC diversi partendo da un'unica chiavetta USB**, senza che chi lo fa debba conoscere Linux, le tabelle delle partizioni o l'interfaccia di Rescuezilla. Configura un PC esattamente come vuoi una sola volta, crea un backup, poi collega la stessa chiavetta a un numero qualsiasi di altri PC e ripristina — ognuno finirà con lo stesso sistema operativo, le stesse applicazioni, le stesse impostazioni, correttamente ridimensionato per riempire qualunque disco su cui sia finito.

Due applicazioni fanno il lavoro vero e proprio, sempre le stesse due sia che tu usi la GUI col mouse sia il menu testuale con la tastiera:

- **PNLUG Restore** — trova il backup sulla chiavetta, individua il disco interno giusto, chiede conferma, ripristina e ridimensiona il filesystem per usare tutto il disco.
- **PNLUG Backup** — sceglie il disco da salvare, restringe la sua partizione principale (a circa 20GB, o al minimo consentito dai dati effettivi) così il backup è più piccolo e veloce, poi lo salva sulla chiavetta.

Entrambe sono già presenti sul desktop live della chiavetta; non serve installare nulla per usarla.

Il sistema live si avvia inoltre direttamente in RAM per impostazione predefinita (è proprio il punto di avere una chiavetta Ventoy — lo strumento continua a funzionare con `restoreimg/` sulla partizione dati della chiavetta, ma la chiavetta stessa non serve più per mantenere il sistema operativo in funzione una volta avviato), quindi va benissimo lasciare la chiavetta collegata per tutta la sessione senza preoccuparsi che la velocità della USB rallenti qualcosa dopo l'avvio.

## 1. Preparare la chiavetta Ventoy

Serve farlo una sola volta per chiavetta (o di nuovo se ricostruisci la ISO — vedi la [Guida alla compilazione](build-guide.it.md)).

1. Procurati una chiavetta USB — 8GB bastano per il solo sistema live; aggiungi la dimensione del tuo backup (un backup ristretto di solito è ben sotto i 20GB) per `restoreimg/`. 32GB o più è una dimensione comoda.
2. Installa [Ventoy](https://www.ventoy.net/en/download.html) sulla chiavetta usando l'installer di Ventoy stesso (`Ventoy2Disk.exe` su Windows, `VentoyWeb.sh`/`Ventoy2Disk.sh` su Linux). Questo formatta la chiavetta e crea due partizioni: una grande partizione exFAT per i dati (dove metterai i file) e una piccola partizione nascosta `VTOYEFI` che Ventoy usa per l'avvio. Segui la [guida ufficiale di Ventoy](https://www.ventoy.net/en/doc_start.html) se è la prima volta — è un passaggio di cinque minuti, una tantum, e non è specifico di questo progetto.
3. Una volta installato Ventoy, la chiavetta appare come una normale unità exFAT. Copia nella sua radice:
   - **`pnlug_zilla.iso`** — la ISO compilata (da `build/output/` se l'hai compilata tu stesso, o scaricata dalla pagina [Releases](../../releases) di questo repository).
   - **Il contenuto della cartella `ventoy/` di questo repository** (`ventoy.json`, `ventoy_backup.json`, e la cartella `theme/`) — copiali in una cartella chiamata letteralmente `ventoy` nella radice della chiavetta, fondendoli con la cartella `ventoy/` che l'installer di Ventoy ha già creato lì. Questo è ciò che dà al menu di avvio il marchio PNLUG e lo fa avviare automaticamente in `pnlug_zilla.iso` dopo 3 secondi se nessuno tocca la tastiera (vedi le chiavi `VTOY_DEFAULT_IMAGE` e `VTOY_MENU_TIMEOUT` in `ventoy.json`, e la [documentazione dei plugin](https://www.ventoy.net/en/plugin_control.html) se vuoi modificarle) — Ventoy mostra comunque un altro menu "quale modalità di avvio", che richiede una singola pressione di Invio (vedi il [passo 2](#2-creare-un-backup-limmagine-campione) più sotto). Imposta anche il layout di tastiera predefinito su italiano (`VTOY_DEFAULT_KBD_LAYOUT`) — cambia quella chiave se le tue tastiere hanno un layout diverso.

Fatto — la chiavetta è pronta. Non serve installare nient'altro su di essa; `restoreimg/` (il backup) viene creato automaticamente la prima volta che usi PNLUG Backup.

## 2. Creare un backup (l'"immagine campione")

Fallo su **un** PC di riferimento, configurato esattamente come vuoi che finisca ogni macchina di destinazione.

1. Collega la chiavetta al PC di riferimento e avvia da essa (vedi il tasto per il menu di avvio del tuo PC — comunemente `F12`, `F11`, `Esc`, oppure `F2`/`Canc` per il setup del BIOS dove puoi riordinare i dispositivi di avvio). Se il Secure Boot è attivo, vedi [Secure Boot](#6-secure-boot) più sotto.
2. Ventoy mostra il suo menu con il tema PNLUG; premi Invio sulla ISO oppure aspetta semplicemente 3 secondi perché venga selezionata automaticamente. Mostra poi un altro menu che chiede "Boot in normal mode / grub2 mode / File checksum" — questo richiede una vera pressione di Invio (è il menu proprio di Ventoy per ogni immagine, non qualcosa controllato dalla configurazione PNLUG); **Boot in normal mode** è già evidenziato, quindi basta un solo Invio.
3. Il desktop live si carica e si apre automaticamente la finestra **PNLUG Auto Rescue** (in realtà è lo strumento di *ripristino* che si avvia da solo — vedi le [FAQ](#si-e-aperta-lapp-sbagliata-restore-invece-di-backup-automaticamente) se vuoi che si apra invece il backup). Chiudila, oppure apri **PNLUG Backup** dall'icona sul desktop.
4. PNLUG Backup elenca i dischi reali (qualsiasi supporto Ventoy/live è nascosto per sicurezza) e consiglia quello che ha effettivamente il tuo sistema operativo — già preselezionato. Conferma che sia il disco giusto e clicca **Continue**.
5. Una finestra di conferma mostra il modello del disco, la dimensione e il contenuto attuale, e — se esiste già un backup sulla chiavetta — avvisa che verrà sostituito. Conferma.
6. Restringe prima la partizione di sistema (può richiedere qualche minuto su un disco grande — è normale), poi esegue il backup vero e proprio, mostrando in diretta progresso e log completo. Quando dice **"Backup completed successfully"**, hai finito.
7. Il backup si trova in `restoreimg/` sulla partizione dati della chiavetta. Copia l'intera cartella da qualche parte al sicuro se vuoi una seconda copia — non è legata a una chiavetta specifica.

## 3. Ripristinare su un PC di destinazione

1. Collega la stessa chiavetta al PC di destinazione e avvia da essa, come sopra.
2. Il desktop live si carica e si apre automaticamente **PNLUG Auto Rescue**. Se non lo fa (vedi [Risoluzione problemi](#la-gui-non-si-apre-mai-automaticamente)), aprila dall'icona sul desktop, oppure usa il menu testuale (vedi [sotto](#4-usare-il-menu-testuale-tui-invece-della-gui)).
3. Cerca automaticamente `restoreimg/` sulla chiavetta. Se lo trova, passa direttamente alla selezione del disco; se no, apre un selettore di cartelle già puntato sulla chiavetta così puoi navigare fino ad esso manualmente.
4. Elenca i dischi reali del PC di destinazione (la chiavetta Ventoy stessa è sempre nascosta qui — non puoi selezionarla per errore) e consiglia il disco fisso più grande, già preselezionato. **Leggi attentamente la finestra di conferma** — mostra il modello del disco, la dimensione e il contenuto attuale, e avvisa che tutto ciò che contiene verrà cancellato definitivamente.
5. Conferma, e il ripristino parte: tabella delle partizioni, poi ogni partizione, poi la partizione di sistema viene ingrandita per riempire lo spazio rimasto sul disco. Il log completo resta visibile per tutta la durata — se qualcosa va storto, vedrai l'errore reale, non solo "è fallito" (vedi [Risoluzione problemi](#restore-o-backup-dice-che-e-fallito---cosa-controllo-davvero)).
6. Quando finisce, propone di riavviare subito il PC, oppure puoi chiudere la finestra e riavviare più tardi.

La macchina ripristinata si avvia con esattamente lo stesso sistema operativo, le stesse applicazioni e le stesse impostazioni del PC di riferimento — con la partizione principale ingrandita automaticamente per usare tutto il nuovo disco, grande o piccolo che sia (purché non sia più piccolo dell'originale — vedi la nota sul controllo delle dimensioni nelle FAQ).

## 4. Usare il menu testuale (TUI) invece della GUI

Se non c'è un display funzionante (o preferisci semplicemente un flusso solo da tastiera), la stessa identica logica di ripristino/backup è disponibile come menu testuale: apri **PNLUG Rescue (Terminal)** dal desktop, oppure si avvia da solo automaticamente se lo strumento grafico non riesce a partire (vedi [Come è stato fatto](how-its-done.it.md) per capire perché esistono entrambi). Fa le stesse domande, mostra le stesse conferme e applica gli stessi controlli di sicurezza — frecce per muoversi, Invio per selezionare.

## 5. Icone sul desktop

Il desktop live ha sempre queste icone, così non devi mai ricordare un comando:

| Icona | Cosa fa |
|---|---|
| **PNLUG Restore** | Apre direttamente la GUI di ripristino. |
| **PNLUG Backup** | Apre direttamente la GUI di backup. |
| **PNLUG Rescue (Terminal)** | Apre la versione a menu testuale (ripristino o backup, a scelta, in un unico strumento). |

## 6. Secure Boot

Se il firmware del PC ha il Secure Boot attivo, vedrai un errore `Verification failed: (0x1A) Security Violation` prima che carichi qualsiasi altra cosa. È previsto, non è specifico di PNLUG — è così che funziona normalmente il supporto Secure Boot di Ventoy la prima volta su un nuovo PC. Due opzioni, entrambe valide:

- **Più semplice:** disattiva il Secure Boot nel setup del firmware del PC, poi avvia la chiavetta normalmente.
- **Mantieni il Secure Boot attivo:** la schermata blu di MokManager che appare al suo posto permette di scegliere *Enroll key from disk* → il file `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` nella radice della chiavetta → registralo → riavvia. È un passaggio una tantum per ogni PC; ogni avvio successivo della chiavetta su quello stesso PC funzionerà normalmente.

Dettagli completi nelle [Note sul Secure Boot](../SECURE_BOOT.it.md).

## Risoluzione problemi / FAQ

#### La chiavetta non compare affatto nel menu di avvio
Controlla l'impostazione della modalità di avvio del firmware del PC (UEFI vs. Legacy/CSM) — Ventoy supporta entrambe, ma se il firmware è bloccato su una e la chiavetta è stata scritta nell'altra modalità potrebbe non comparire come opzione di avvio. Verifica anche che la chiavetta sia effettivamente collegata a una porta USB che il firmware analizza all'avvio (alcuni PC controllano solo certe porte prima che il sistema operativo si carichi).

#### Ricevo un errore di Secure Boot / "Security Violation"
Vedi [Secure Boot](#6-secure-boot) sopra — non è un problema della chiavetta, è il comportamento previsto al primo avvio.

#### "Couldn't find restoreimg/ automatically" — si apre un selettore di cartelle
Significa che lo strumento non ha trovato una partizione contrassegnata da Ventoy con una cartella `restoreimg/`. Più spesso significa che:
- Il backup non è ancora stato creato su questa chiavetta (vedi [passo 2](#2-creare-un-backup-limmagine-campione)).
- Stai usando una chiavetta diversa da quella su cui è stato creato il backup.
- La cartella `restoreimg/` è stata spostata, rinominata o cancellata.

Nel selettore di cartelle, naviga semplicemente fino a dove si trova davvero il tuo backup e clicca **Use this folder** — tutto il resto funziona allo stesso modo da lì in poi.

#### Il disco di destinazione che voglio non è nell'elenco, o è disabilitato con un avviso sulla dimensione
Lo strumento rifiuta una destinazione più piccola del disco di cui è stato fatto il backup, per evitare un ripristino che fallisce silenziosamente a metà. Se il disco di destinazione è davvero più piccolo di quello del PC sorgente, dovrai usare un disco più grande, oppure creare prima un backup più piccolo sul PC di riferimento (restringi manualmente le sue partizioni prima del backup, oltre al restringimento automatico a circa 20GB già applicato).

#### Restore o backup dice che è "fallito" — cosa controllo davvero?
Lo strumento è deliberatamente dettagliato in caso di errore: il messaggio include le vere ultime righe di output del motore di ripristino/backup sottostante, non solo "è fallito". Leggi prima quel dettaglio — le cause più comuni sono un disco che si sta guastando/scollegando durante l'operazione, oppure lo spazio esaurito sulla destinazione (per i backup, se la chiavetta stessa è quasi piena). Il log completo della sessione resta visibile sopra l'errore per tutto il tempo; scorri in alto se ti serve più contesto.

#### Il PC ripristinato non si avvia dopo
- Assicurati che la modalità di avvio del firmware del PC di destinazione (UEFI/Legacy) sia impostata come quella del PC di riferimento — un disco ripristinato mantiene qualunque metodo di avvio con cui era stato originariamente installato.
- Se il PC di riferimento usava il Secure Boot con le proprie chiavi registrate, nota che la firma del bootloader del disco ripristinato non cambia — il comportamento del Secure Boot sul PC di destinazione segue le impostazioni del firmware *di destinazione*, non quelle di origine.
- Il passaggio di ripristino **non** reinstalla deliberatamente il bootloader (ripristina il settore/le partizioni di avvio del disco così come sono state salvate nel backup) — se il primissimo record di avvio era in qualche modo specifico dell'hardware originale, è la prima cosa da controllare.

#### Si è aperta l'app sbagliata (Restore invece di Backup) automaticamente
Quella che si apre automaticamente all'avvio del desktop è sempre **Restore** — corrisponde al caso d'uso reale principale (la maggior parte degli avvii di una chiavetta finita servono a installare l'immagine campione su un nuovo PC, non a rifarla). Per creare un nuovo backup, chiudi semplicemente la finestra di ripristino avviata automaticamente e apri invece **PNLUG Backup** dall'icona sul desktop — non serve cambiare nient'altro.

#### La GUI non si apre mai automaticamente
Dovrebbe avviarsi da sola pochi secondi dopo la comparsa del desktop. Se davvero non lo fa (e non sta solo ancora caricando — la comparsa delle icone sul desktop non significa che tutto abbia finito di avviarsi), significa che lo strumento grafico non è riuscito a partire affatto — in quel caso dovrebbe essere già passato automaticamente al menu testuale in una finestra di terminale, e infine a un semplice messaggio di errore con `/tmp/pnlug-gui-autostart.log` per i dettagli se anche quello fallisce. Cerca quel terminale o quella finestra di errore; se non vedi nessuno dei due, apri manualmente **PNLUG Rescue (Terminal)** dal desktop — fa esattamente la stessa cosa.

#### Posso fare un backup e un ripristino dalla stessa chiavetta, uno dopo l'altro?
Sì — è esattamente il punto. Usare la chiavetta per un backup non cambia nulla nell'usarla successivamente per un ripristino, o viceversa, e non serve "azzerare" la chiavetta tra le due operazioni.

#### Dove sono i log?
`/tmp/pnlug-gui-autostart.log` nella sessione live copre la catena di avvio automatico (quale strumento ha provato ad avviarsi, e perché è scattato un fallback, se è successo). Sia la GUI che la TUI mantengono il log completo dell'operazione visibile sullo schermo per tutta la durata dell'esecuzione — niente scorre via o viene nascosto, nemmeno in caso di successo.

#### Questo tocca la rete, il Wi-Fi o gli indirizzi MAC del PC di destinazione?
Non viene modificata nessuna configurazione di rete né alcun indirizzo MAC. Se la tua immagine campione includeva strumenti per questo (per esempio uno script al primo avvio che rigenera identificatori univoci della macchina), quelli vengono eseguiti come parte del normale processo di avvio del sistema operativo ripristinato, non come parte di questo strumento — il ripristino si limita a riscrivere il contenuto del disco e a ingrandire la partizione.
