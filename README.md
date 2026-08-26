<p align="center">
  <img src="custom_components/casa_es_energy_manager/brand/icon.png" width="128" alt="Logo Casa ES Energy Manager">
</p>

# Casa ES Energy Manager

**Casa ES Energy Manager** è un'integrazione personalizzata per Home Assistant dedicata alla gestione intelligente di un impianto fotovoltaico con batteria, inverter e rete trifase.

> **Versione 1.4.3 — gestione di emergenza guidata e recupero batteria legato al sole**
>
> Le decisioni di sicurezza elettrica e il controllo reale restano deterministici e locali. Il pianificatore AI è soltanto consultivo e non può superare i limiti di rete, inverter, fase, batteria o le regole configurate sui dispositivi.

> **NOTA BENE**  
> Nell'interfaccia di configurazione l'asterisco `*` indica sempre un campo **facoltativo**. I campi senza asterisco sono **obbligatori**.

## A cosa serve

Casa ES Energy Manager osserva in tempo reale produzione FV, consumi della casa, rete, batteria e carico delle singole fasi. Usa questi dati per:

- massimizzare l'autoconsumo del fotovoltaico;
- proteggere rete, inverter e singole fasi da sovraccarichi;
- cercare di raggiungere il SOC batteria desiderato entro l'ora configurata;
- continuare a recuperare il target dopo l'ora prevista se esiste ancora un'opportunità solare utile;
- gestire automaticamente carichi flessibili rispettando priorità, SOC, tempi minimi ON/OFF e vincoli giornalieri;
- apprendere nel tempo il consumo reale dei dispositivi variabili;
- monitorare elettrodomestici non normalmente gestiti e, opzionalmente, fermarli o metterli in pausa solo durante una vera emergenza elettrica;
- fornire al pianificatore AI un contesto completo senza permettergli di aggirare le protezioni deterministiche.

## Architettura di sicurezza

L'ordine di priorità è sempre:

1. limiti reali misurati di rete, fase e inverter;
2. protezione elettrica deterministica;
3. target batteria e preferenza energetica scelta dall'utente;
4. vincoli e anti-ciclaggio dei dispositivi gestiti;
5. previsione fotovoltaica e ottimizzazione energetica;
6. pianificatore AI consultivo.

Le misure reali di rete, inverter e fasi sono sempre autorevoli. Le stime dei singoli dispositivi servono per attribuire e pianificare, ma non vengono sommate sopra i totali reali misurati.

## Installazione e aggiornamento con HACS

Aggiungi questo repository a HACS come repository personalizzato di tipo **Integrazione**, installa **Casa ES Energy Manager** e riavvia Home Assistant.

Poi apri:

`Impostazioni → Dispositivi e servizi → Casa ES Energy Manager`

Le configurazioni delle versioni precedenti restano leggibili. Quando modifichi un vecchio carico monitorato, la v1.4.3 riconosce anche le configurazioni di emergenza già presenti e le presenta nella nuova procedura guidata.

## Configurazione iniziale

La configurazione principale è divisa in sei sezioni.

### 1. Sensori elettrici

Campi obbligatori:

- **Potenza FV reale**: produzione realmente erogata dall'inverter;
- **Potenza totale carichi casa**: consumo totale reale;
- **Potenza rete**: positivo = prelievo, negativo = immissione;
- **SOC batteria**: percentuale reale di carica;
- **Potenza batteria**: positivo = carica, negativo = scarica.

Campi facoltativi ma fortemente consigliati su un impianto trifase:

- **Potenza fase L1 \***;
- **Potenza fase L2 \***;
- **Potenza fase L3 \***.

I sensori di fase permettono a Casa ES di capire quale fase è realmente in difficoltà e di intervenire solo sui carichi che possono risolvere quel problema.

### 2. Limiti e protezioni

- **Limite totale inverter (W)**: potenza totale massima considerata disponibile dall'inverter;
- **Limite operativo per singola fase (W)**: soglia massima di ogni fase;
- **Limite operativo rete (W)**: soglia massima di prelievo totale;
- **Margine di sicurezza (W)**: margine sottratto ai limiti per non lavorare esattamente sulla soglia.

Questi limiti hanno sempre precedenza su previsione, batteria e AI.

### 3. Previsione fotovoltaica

Tutti i campi sono facoltativi:

- **FV potenziale adesso \***;
- **Energia FV residua oggi \***;
- **Previsione ora corrente \***;
- **Previsione prossima ora \***;
- **Energia FV prevista oggi \***;
- **Energia FV prevista domani \***;
- **Entità meteo \***.

Le previsioni aiutano a decidere quanto essere prudenti, ma non sostituiscono mai le misure elettriche reali.

### 4. Batteria e strategia energetica

- **Capacità batteria (kWh)**: capacità utile installata;
- **SOC obiettivo batteria (%)**: percentuale desiderata;
- **Ora entro cui raggiungere l'obiettivo**: scadenza desiderata della giornata;
- **Consumo base medio previsto casa (W)**: energia inevitabile da riservare;
- **Efficienza stimata di carica (%)**;
- **Preferenza energetica**;
- parametri della ricarica manuale di emergenza da rete;
- **Script avvio ricarica da rete \*** e **Script arresto ricarica da rete \***, facoltativi.

#### Preferenza energetica

- **Batteria prioritaria**: mantiene un margine maggiore prima di ammettere carichi flessibili;
- **Bilanciata**: compromesso tra completamento batteria e utilizzo dei carichi;
- **Carichi prioritari**: usa più facilmente il surplus disponibile, senza indebolire le protezioni elettriche.

### 5. Pianificatore AI

- **Abilita pianificatore AI consultivo**;
- **Intervallo pianificatore AI (min)**;
- **Sensori aggiuntivi per AI \***;
- **Entità AI Task / Gemini \***.

L'AI può proporre una strategia e spiegare il motivo, ma la decisione fisica finale resta deterministica.

### 6. Riepilogo

Salva la configurazione. In seguito puoi aggiungere separatamente **Dispositivi gestiti** e **Carichi monitorati**.

## Obiettivo batteria giornaliero

L'ora configurata è una **scadenza desiderata**, non il momento in cui Casa ES smette di interessarsi alla batteria.

La v1.4.3 usa questa logica:

- prima dell'ora target, pianifica per raggiungere il SOC desiderato entro quella scadenza;
- se all'ora target il SOC non è stato raggiunto, continua a recuperarlo **solo finché esiste ancora un'opportunità solare utile**;
- se il target era stato raggiunto e nel pomeriggio la batteria scende, può recuperare nuovamente il SOC quando torna disponibile FV utile;
- una nuvola temporanea non chiude subito il recupero se FV potenziale o previsione residua indicano altra produzione nella giornata;
- quando FV reale, FV potenziale e previsione residua indicano che la giornata solare è terminata, Casa ES chiude il recupero del target di oggi;
- dopo la fine dell'opportunità solare non cerca di mantenere artificialmente il 100% durante la notte e non autorizza una ricarica da rete soltanto per recuperare il target pomeridiano;
- a mezzanotte inizia il nuovo ciclo giornaliero verso l'ora target del nuovo giorno.

La ricarica da rete resta una funzione separata ed esplicita, legata agli script configurati dall'utente.

## Dispositivi gestiti

Un **Dispositivo gestito** può essere realmente acceso o spento da Casa ES quando il controllo automatico reale è abilitato.

Principali impostazioni:

- nome;
- entità Home Assistant da comandare;
- sensore di potenza reale `*`;
- tipo di dispositivo;
- potenza nominale iniziale/di riserva;
- apprendimento automatico della potenza reale;
- priorità da `1` a `100` (`1` = massima);
- fase elettrica;
- SOC batteria minimo;
- eventuale integrazione dalla rete e relativo limite;
- durata tipica `*`;
- tempo minimo acceso `*`;
- tempo minimo spento `*`;
- tempi giornalieri e finestre orarie;
- numero massimo di attivazioni;
- comportamento non interrompibile;
- limite di scarica batteria durante il funzionamento.

### Climatizzatori e pompe di calore

Per un dispositivo di tipo climatizzatore/PDC Casa ES mantiene profili di consumo separati per modalità, ad esempio:

- raffrescamento (`cool`);
- riscaldamento (`heat`);
- deumidificazione (`dry`);
- sola ventilazione (`fan_only`).

Se l'entità comandata è già `climate.*`, può essere usata anche come riferimento della modalità.

Se invece il comando reale è uno `switch.*`, ad esempio uno switch che comanda un gruppo di climatizzatori, scegli un'entità `climate.*` dello stesso impianto come **Climatizzatore di riferimento per la modalità**. Casa ES continuerà a comandare lo switch e userà il climate soltanto per identificare il profilo energetico corretto.

## Modalità Automatico, Manuale e Spento

Ogni dispositivo gestito dispone della propria modalità:

- **Automatico**: Casa ES può accenderlo e spegnerlo;
- **Manuale**: Casa ES osserva e può continuare ad apprendere, ma non invia comandi al dispositivo;
- **Spento**: il dispositivo viene escluso dalla normale ottimizzazione automatica.

## Controllo automatico reale

L'integrazione espone lo switch **Controllo automatico reale**.

Con il controllo reale disattivato, Casa ES continua a monitorare, calcolare e apprendere, ma non invia comandi fisici ai dispositivi né ai carichi monitorati in emergenza.

Con il controllo reale attivato:

- i dispositivi in Automatico possono essere gestiti;
- quelli in Manuale non vengono comandati;
- i normali cambi di stato rispettano i tempi minimi ON/OFF;
- la protezione di fase/inverter resta prioritaria;
- un allarme di potenza totale rete segue l'ordine di emergenza descritto sotto;
- viene inviato al massimo un comando per aggiornamento del coordinatore, così il sistema può misurare nuovamente l'effetto prima di decidere un'altra azione.

## Carichi monitorati

Un **Carico monitorato** serve prima di tutto a spiegare il consumo reale di una fase. Non entra nella normale ottimizzazione FV/batteria e non viene comandato dall'AI.

Campi base:

- **Nome del carico**;
- **Sensore potenza reale**;
- **Fase elettrica**;
- **Abilita monitoraggio**;
- **Gestibile in emergenza**.

Se **Gestibile in emergenza** è disattivato, il carico resta completamente in sola lettura.

Se è attivato, compare una procedura guidata con tre modalità.

### Switch ON/OFF

Usa un singolo `switch.*`.

In emergenza Casa ES porta lo switch su OFF. Dopo almeno 2 minuti di situazione elettrica stabile può riportarlo automaticamente su ON.

Esempio tipico: stufetta o altro carico che può essere interrotto e riavviato senza perdere un ciclo.

### Pausa + riprendi

Configura due comandi distinti:

- **Comando pausa**;
- **Comando riprendi**.

Possono essere entità compatibili come `button.*`, `script.*` o altre entità supportate. Casa ES mette in pausa il ciclo durante l'emergenza e usa il comando di ripresa soltanto dopo almeno 2 minuti di stabilità.

Esempio tipico: lavastoviglie, lavatrice o asciugatrice quando l'integrazione espone veri comandi di pausa e ripresa.

### Solo arresto

Configura soltanto il comando di arresto.

Casa ES può fermare il carico durante l'emergenza, ma **non lo riavvia automaticamente**. Quando la situazione torna sicura viene richiesto il ripristino manuale.

Esempio tipico: forno o altro apparecchio che è sicuro spegnere ma che non deve ripartire da solo.

## Ordine degli interventi in emergenza elettrica

La scelta dipende dal problema elettrico reale, non da una semplice lista di priorità fissa.

### Potenza totale rete / Enel

1. Casa ES cerca prima carichi monitorati attivi e configurati come gestibili in emergenza;
2. prova a liberare la potenza necessaria senza spegnere più carichi del necessario;
3. invia un comando e misura nuovamente la situazione;
4. se non basta, può passare ad altri carichi utili;
5. i dispositivi normalmente gestiti continuano a rispettare le regole e i tempi minimi concordati per questo caso.

### Sovraccarico di una fase o dell'inverter

1. Casa ES prova prima i dispositivi gestiti che possono realmente alleggerire la fase interessata o l'inverter;
2. misura nuovamente dopo il comando;
3. se non basta, usa i carichi monitorati gestibili in emergenza che possono risolvere quel preciso problema elettrico.

Un carico su una fase diversa non viene scelto per risolvere un sovraccarico di singola fase se non può produrre alcun beneficio reale.

## Contatori di potenza condivisi

Se più dispositivi condividono lo stesso sensore di potenza, Casa ES usa una gestione conservativa:

- non attribuisce automaticamente gli stessi watt a più dispositivi;
- non usa il contatore condiviso per apprendimento adattivo individuale;
- conta quel consumo una sola volta nell'attribuzione di fase;
- se non è possibile stabilire con sicurezza quale dispositivo sia responsabile, lascia il consumo nella voce di carico non attribuito della fase.

## Apprendimento adattivo della potenza

Quando un dispositivo dispone di un sensore dedicato e l'apprendimento è abilitato, Casa ES costruisce nel tempo un profilo reale del consumo.

Lo stato `ready` significa che il profilo contiene abbastanza campioni per essere usato: l'apprendimento **continua comunque** durante gli utilizzi futuri.

I consumi di standby non diventano campioni attivi. Per i climatizzatori i profili vengono mantenuti separati per modalità. Picchi isolati anomali vengono limitati per non gonfiare permanentemente la stima usata nelle decisioni successive.

## Pianificatore AI consultivo

Il pianificatore AI riceve il contesto energetico più recente, compresi:

- produzione e previsione FV;
- SOC e potenza batteria;
- margini rete, inverter e fasi;
- attribuzione dei carichi;
- profili adattivi;
- policy deterministica corrente.

L'AI può consigliare una strategia, ma non può autorizzare una condizione che la policy deterministica considera pericolosa o non consentita.

## Ricarica manuale di emergenza da rete

La ricarica batteria da rete resta intenzionalmente esplicita e dipendente dall'inverter.

Per abilitarla configura nelle Opzioni due `script.*`:

- script di avvio;
- script di arresto.

Lo script di avvio riceve:

- `power_w`;
- `target_soc`;
- `max_minutes`.

Casa ES può richiedere lo stop quando viene raggiunto il SOC desiderato, scade il tempo massimo o una protezione elettrica lo richiede.

Se gli script non sono configurati, la funzione resta indisponibile.

## Diagnostica

La diagnostica si scarica da:

`Impostazioni → Dispositivi e servizi → Casa ES Energy Manager → Scarica diagnostica`

Tra i dati utili trovi:

- sensori sorgente e disponibilità;
- limiti configurati;
- margini rete/inverter/fasi;
- previsione e FV potenziale;
- policy del target batteria;
- dispositivi gestiti e decisioni correnti;
- carichi monitorati e relativa modalità di emergenza;
- eventuali carichi sganciati e ripristini pendenti;
- profili di apprendimento adattivo;
- ultimo comando reale e relativo motivo;
- stato e consiglio del pianificatore AI.

## Aggiornamento alla v1.4.3

Dopo l'aggiornamento e il riavvio di Home Assistant:

1. verifica lo stato dello switch **Controllo automatico reale**;
2. i carichi monitorati esistenti continuano a funzionare;
3. aprendo la modifica di un carico monitorato, Casa ES riconosce la vecchia configurazione e propone la nuova gestione guidata;
4. scegli **Switch ON/OFF**, **Pausa + riprendi** o **Solo arresto** a seconda delle capacità reali dell'elettrodomestico;
5. verifica sempre manualmente che i pulsanti di pausa, stop e ripresa dell'integrazione dell'elettrodomestico funzionino davvero durante un ciclo prima di considerarli affidabili in emergenza;
6. controlla una nuova diagnostica dopo le prime prove reali.

## Origine e licenza

Casa ES Energy Manager è un progetto modificato/derivato da `InventoCasa/PV-Excess-Control` e mantiene gli obblighi della licenza **GNU Affero General Public License v3**.

Consulta `LICENSE` e `NOTICE.md` per i dettagli.
