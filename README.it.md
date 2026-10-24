# EV LFP Battery Coach per Home Assistant

**Calibrazione automatica e sicura delle batterie LFP (litio-ferro-fosfato) delle auto elettriche.**
Tieni il limite di carica quotidiano basso e lascia che Home Assistant porti l'auto al 100% ogni
due settimane, aspetti che il BMS bilanci le celle e rimetta il limite a posto. Zero YAML.

🇬🇧 [English version](README.md)

> [!WARNING]
> **Alpha (0.x).** Sviluppato su una **Tesla Model 3** (integrazione Tesla: stati di ricarica
> `charging`, `complete`, `stopped`, `disconnected`, `no_power`, `starting`) e testato con una suite
> automatica che simula il veicolo. Un ciclo di ricarica completo reale è ancora in conferma. Altre marche (BYD, MG, Volvo, ...) possono segnalare la fine
> della ricarica in modo diverso e il rilevamento di "finita" potrebbe richiedere una taratura.
> Segnalaci cosa fa la tua auto.

---

## Il problema

Le batterie LFP hanno una curva di tensione molto piatta: il BMS stima bene lo stato di carica solo
se ogni tanto vede una carica completa e bilancia le celle. I costruttori consigliano il 100% ogni
1–2 settimane; per l'uso quotidiano, invece, un limite intorno al 70–80% è più gentile con la
batteria. Fare il cambio a mano è facile da dimenticare, e automatizzarlo in modo ingenuo nasconde
una trappola:

> Se un'automazione abbassa il limite al 75% **nell'istante** in cui la batteria tocca il 100%,
> l'auto smette di caricare e il BMS non completa il **top balancing** a bassa potenza
> (di solito 15–30 minuti).

EV LFP Battery Coach è costruito attorno a questa trappola.

## Come funziona

1. **Scadenza.** La calibrazione diventa dovuta a mezzanotte (ora locale) dell'N-esimo giorno dopo
   la precedente (14 di default). Contare giorni di calendario evita che le ricariche serali
   spostino la scadenza di un giorno a ogni ciclo.
2. **Avvio.** Con l'auto **a casa e collegata** e la calibrazione dovuta, il coach porta il limite
   al valore di calibrazione e ti avvisa.
3. **Top balancing.** Quando la batteria raggiunge il target il limite non viene toccato. La fase
   termina solo quando accade **una** di queste cose:
   - lo stato di ricarica indica "finita" (`complete`, `stopped`, ... configurabile) **e** è passato
     il *tempo minimo al massimo* (30 min di default). È il comportamento predefinito: è ciò che
     l'integrazione Tesla riporta quando l'auto smette di caricare;
   - opzionale, se la wallbox è connessa a Home Assistant: l'auto ha smesso di **assorbire
     corrente** (potenza ≤ ~50 W per 3 minuti, dopo il tempo minimo). È più preciso dello stato,
     che in quel caso viene ignorato;
   - il cavo viene scollegato;
   - scade il *timeout di sicurezza* (45 min), qualunque cosa riporti l'auto.
4. **Fine.** Dopo un breve ritardo il limite quotidiano viene ripristinato, la calibrazione viene
   registrata e ricevi la notifica.

### Pensato per essere sicuro

| Situazione | Cosa fa il coach |
| --- | --- |
| Un sensore è `unavailable` / `unknown` / non numerico | **Non decide nulla.** Non viene mai letto come 0 %, "scollegato" o "fuori casa". |
| L'auto va offline durante il bilanciamento | Continua ad aspettare; solo "scollegato" esplicito, stato finale o timeout chiudono la fase. |
| Riavvio di Home Assistant o reload a metà ciclo | Fase, orario di inizio e flag sono salvati; i timer si ricostruiscono. Il limite non può restare bloccato al 100%. |
| L'integrazione dell'auto non è ancora caricata all'avvio | Nessuna decisione finché Home Assistant non è completamente avviato. |
| Raffica di aggiornamenti dall'auto | Debounce e serializzazione: un comando, una notifica. |
| Il cloud è in ritardo sul comando | Il comando non viene ripetuto prima di 5 minuti. |
| L'auto non risponde (dorme, rate limit) | Riprova ogni 5 minuti, apre un problema in **Riparazioni** dopo 3 errori e si riprende da sola. |
| Scolleghi / esci durante la calibrazione | Dopo 12 h (configurabile) torna il limite quotidiano e si riprova alla prossima ricarica. |
| Fai tu una carica al 100% | Conta come calibrazione; il limite viene ripristinato dopo (disattivabile). |

## Installazione

**HACS:** ⋮ → *Repository personalizzati* → aggiungi
`https://github.com/ugidj/ha-ev-lfp-coach` come **Integrazione** → scarica → riavvia →
*Impostazioni → Dispositivi e servizi → Aggiungi integrazione → EV LFP Battery Coach*.

**Manuale:** copia `custom_components/ev_lfp_coach` in `<config>/custom_components/` e riavvia.

Richiede Home Assistant **2026.1.0** o successivo (sviluppato e testato su 2026.10).

## Configurazione

Tutto da interfaccia, una voce per veicolo.

- **Veicolo:** livello batteria, limite di carica (entità `number`; un solo coach per entità) e
  stato di ricarica. Il resto sta in una sezione chiusa *Impostazioni opzionali*: cavo collegato,
  **«Calibra solo quando l'auto è a casa»** (spento di default: la calibrazione avviene anche se
  ricarichi fuori casa; se lo accendi scegli posizione e zona) e sensore di potenza (utile solo se
  la wallbox è connessa a Home Assistant).
- **Parametri** (modificabili sempre da **Configura**; limite quotidiano, limite di calibrazione e
  intervallo anche con i cursori in dashboard): limite quotidiano 75 %, limite di calibrazione
  100 %, intervallo 14 giorni, tempo minimo al massimo 10 min, timeout 45 min, ritardo prima del
  ripristino 30 s, stati che significano "finita", rinuncia dopo 12 h, ripristino dopo carica
  manuale, notifiche (entità notify, servizio `notify.*` o notifica persistente).
- **Riconfigura:** se cambi integrazione dell'auto o le entità vengono rinominate, usa
  *Riconfigura*: lo storico delle calibrazioni resta.

## Entità, servizi, eventi

- Sensori: stato calibrazione (`calibrated`, `calibration_due`, `calibrating_100`,
  `top_balancing`, con storico negli attributi), giorni dall'ultima calibrazione, ultima e prossima
  calibrazione.
- Sensori binari: calibrazione necessaria, calibrazione in corso.
- Pulsanti: *Calibra alla prossima ricarica*, *Annulla calibrazione*, *Segna come calibrata ora*.
- Number: limite quotidiano, limite di calibrazione, intervallo. Switch: *Gestione automatica*.
- Servizi `ev_lfp_coach.start_calibration`, `cancel_calibration`, `mark_calibrated` (target: il
  sensore *Stato calibrazione*).
- Eventi `ev_lfp_coach_calibration_started`, `_balancing_started`, `_calibration_completed`,
  `_calibration_aborted`, utili per le tue automazioni (vedi la versione inglese per i payload).

Gli entity ID nascono dai nomi delle entità nella lingua di Home Assistant al momento della
creazione, quindi su un'installazione italiana saranno in italiano.

## Compatibilità

Il coach parla solo con *entità*: funziona con qualunque integrazione le fornisca. Gli stati
predefiniti di "finita" corrispondono alle integrazioni **Tesla** (Teslemetry, Tesla Fleet). Per le
altre marche guarda i valori del sensore di stato ricarica e aggiungi nelle opzioni quelli che
indicano "ha smesso di caricare". Le segnalazioni dal mondo reale sono benvenute (template
*Vehicle compatibility report*).

Le raccomandazioni di calibrazione variano per costruttore: verifica quella del tuo modello. Questa
integrazione automatizza solo la procedura che configuri e non offre alcuna garanzia (vedi licenza).

## Risoluzione problemi

Scarica la diagnostica (⋮ sull'integrazione): la sezione `snapshot` mostra cosa vede il coach in
questo momento. Per il log di debug:

```yaml
logger:
  logs:
    custom_components.ev_lfp_coach: debug
```

## Licenza

MIT — vedi [LICENSE](LICENSE).
