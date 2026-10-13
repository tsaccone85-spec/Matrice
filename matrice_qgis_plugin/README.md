# Matrice - Rilievo di Scavo (plugin QGIS)

Scheletro di codice generato dalla specifica tecnica "Plugin QGIS per Matrice"
(sezione 10), allineato allo schema **reale** del database di produzione
Matrice (Supabase/PostgreSQL/PostGIS, verificato direttamente sul progetto
prima di scrivere qualunque migrazione). Segue la struttura MVP della
roadmap (sezione 11): login, connessione a Matrice, apertura tavola,
editing con salvataggio automatico.

## Tabelle coinvolte (schema reale)

Gia' esistenti in Matrice, usate in sola lettura o in scrittura mirata:
- `cantiere`, `cantiere_accesso`, `utente`
- `us` (con la nuova colonna `attiva`, vedi sotto), `sepoltura`,
  `sepoltura_us`, `reperto`

Nuove, create per questo plugin (migrazioni gia' applicate su Supabase):
- `rilievo_geometria`, `rilievo_quota` -- geometria PostGIS vera e propria,
  con il campo aggiuntivo `layer` su `rilievo_geometria` per la
  caratterizzazione grafica (sezione 6/7)
- `us_storico_modifiche` -- log immutabile di ogni scrittura fatta da QGIS
  (sostituisce il nome provvisorio "log_modifiche" usato nelle prime bozze)
- `v_rilievo_geometria_qgis` -- vista di lettura che unisce
  `rilievo_geometria` a `us`/`sepoltura_us`/`sepoltura` per portare in QGIS
  solo i campi essenziali (numero_us, definizione, fase, sepoltura_id,
  numero_tomba, layer), non l'intera scheda US (principio "elenco")
- `v_carotaggio_qgis`, `v_colonna_stratigrafica_qgis`,
  `v_sezione_prospetto_qgis` -- viste aggiornabili (con trigger
  `INSTEAD OF UPDATE`) che espongono un campo `geom` calcolato dalle
  colonne `lat`/`lng` aggiunte a `carotaggio`, `colonna_stratigrafica`,
  `sezione_prospetto`; muovere il punto in QGIS scrive lat/lng sulla
  tabella reale
- `cantiere.dem_url` -- riferimento al DEM del cantiere per il ricalcolo
  automatico della quota (vedi sotto)
- `us.attiva` -- booleano (default `true`) per la vista "US attive/non
  attive" di sezione 8; non esisteva in produzione, e' stato aggiunto con
  una migrazione additiva dedicata

**Non esistono** (e non vanno mai referenziate nel codice): una tabella
`fase` a se stante (`fase` e' un campo testo libero su `us`), un campo
`us.tipo_us` (il campo reale si chiama `definizione`), un campo diretto
`us.id_sepoltura` (il legame passa dalla tabella ponte `sepoltura_us`).

## Autenticazione e accesso ai dati (sezione 1/2)

Il login del plugin usa **Supabase Auth** (email + password), lo stesso
meccanismo gia' usato dall'app web di Matrice -- non una tabella `utente`
interrogata direttamente, ne' un microservizio scritto apposta per il
plugin. L'URL del progetto e la chiave pubblica sono precompilati
(`core/supabase_config.py`) e normalmente non vanno toccati.

Le letture che dipendono dai permessi dell'utente loggato -- cantieri
abilitati, elenco fasi, elenco US -- passano da **PostgREST** (le stesse
tabelle, ma via HTTP con il token dell'utente invece che con una query SQL
diretta): e' il database stesso, tramite le policy di Row Level Security
gia' presenti in Matrice, a decidere quali righe restituire. Questo
soddisfa la "Decisione presa" di sezione 1 ("si procede con il servizio
intermedio, non con la connessione diretta") senza dover costruire e
mantenere un backend applicativo separato: Supabase svolge gia' quel
ruolo, con autenticazione, permessi e log applicati dal database ad ogni
richiesta.

La connessione PostgreSQL diretta (`core/db_connector.py`) resta invece
riservata a cio' che il provider "postgres" di QGIS richiede davvero come
connessione diretta: caricare/editare i layer PostGIS in mappa, e
scrivere la riga di log in `us_storico_modifiche` ad ogni commit. Usa un
utente database **tecnico** condiviso e a permessi limitati, impostato
una sola volta da chi gestisce Matrice -- non le credenziali personali
dell'operatore, che restano solo quelle del login.

Implementato in `core/auth.py` (login/rinnovo token via
`core/supabase_client.py`) e `core/matrice_api.py` (letture via
PostgREST); `core/db_connector.py` copre solo la connessione tecnica.

## Regola quota su carotaggi/colonne/sezioni (sezione 5)

Quando l'operatore sposta uno di questi tre punti in QGIS:
- se ha **anche** modificato manualmente `quota_slm` nello stesso commit,
  quel valore vince e non viene toccato;
- altrimenti la quota viene **ricalcolata dal DEM** del cantiere
  (`core/dem_sampler.py`) nella nuova posizione. Se il cantiere non ha un
  DEM configurato, o il punto cade fuori dalla sua copertura, il plugin
  scrive comunque la nuova posizione, lascia la quota precedente e avvisa
  l'operatore in linguaggio semplice (mai un errore tecnico grezzo).

Implementato in `core/point_position_sync.py`, collegato ai segnali di
editing dei tre layer puntuali (distinto dal percorso generico di
`core/sync_handler.py` usato per `rilievo_geometria`/`rilievo_quota`).

## Caratterizzazione grafica (sezione 6)

`config/styles/` contiene tre file .qml forniti dall'utente, tutti
categorizzati sul campo `layer` di `rilievo_geometria`/`v_rilievo_geometria_qgis`
(rinominato da "Layer" a "layer" rispetto all'originale, per combaciare con
il nome reale della colonna -- QGIS e' case-sensitive sui nomi di campo):

- `US.qml` -- stile di base (categorie per materiale/tipologia: Laterizio,
  Pietra, USM, Taglio, ...), applicato automaticamente a tutto il layer
  `rilievo_geometria` all'apertura della tavola, prima ancora che
  l'operatore scelga una vista rapida;
- `Sepolture.qml` -- usato dalla vista rapida "Vedi solo le sepolture";
- `USM.qml` -- usato dalla vista rapida "Vedi solo le USM".

La vista rapida "Vista per fasi" non ha invece un .qml dedicato: il campo
`fase` e' testo libero senza colore proprio salvato su Matrice, quindi il
plugin assegna un colore **casuale ma sempre distinto** a ciascuna fase a
ogni apertura (`StyleManager.random_distinct_colors`, angolo aureo sulla
ruota HSV con punto di partenza casuale), cosi' due fasi non risultano mai
dello stesso colore ne' facilmente confondibili tra loro.

## Cosa e' gia' funzionante nello scheletro

- Struttura del plugin (`metadata.txt`, `__init__.py`, ciclo di vita QGIS)
- Dock widget con cantieri, fasi (elenco), US attive/non attive, vista rapida
- Login reale via Supabase Auth, con messaggi di errore in linguaggio
  semplice (`core/auth.py`, `core/supabase_client.py`)
- Letture filtrate sui permessi utente via PostgREST -- cantieri, fasi,
  elenco US -- allineate allo schema reale (`core/matrice_api.py`)
- Connessione PostgreSQL/PostGIS tecnica per i soli layer in mappa
  (`core/db_connector.py`)
- Caricamento layer come layer PostGIS nativi, nessun file intermedio
  (`core/layer_manager.py`, mappa `LAYER_SOURCES` su viste/tabelle reali).
  All'apertura tavola vengono aggiunti in mappa solo i livelli che hanno
  gia' dei dati per quel cantiere; gli altri restano disponibili a
  richiesta nel pannello "Livelli del cantiere" (spunta per aggiungerli
  vuoti e iniziare a digitalizzare)
- Intercettazione dei commit di editing verso `us_storico_modifiche`, con
  tracciamento del valore precedente della geometria, divisa su due
  percorsi (`core/sync_handler.py` per la geometria US,
  `core/point_position_sync.py` per i tre punti con lat/lng + DEM)
- Le tre query prestabilite (Vista per fasi, Vedi solo le sepolture, Vedi
  solo le USM) definite in `config/quick_views.json` e applicate da
  `core/quick_views.py`, sui campi reali della vista
  `v_rilievo_geometria_qgis`
- "Unisci dati elenchi" (toolbar): con il layer "Rilievo vettoriale" di un
  cantiere attivo selezionato, aggancia le schede US di Matrice al rilievo
  sul numero US tramite un join QGIS in sola lettura (campi con prefisso
  `us_`, es. `us_definizione`); nessuna scrittura su Matrice
  (`core/join_elenchi.py`)
- Import GPS differenziale: CSV, GPX (waypoint), SHP e DXF di
  post-processamento, con dialogo dedicato (pulsante "Importa rilievo
  GPS..." nel dock -> `ui/gps_import_dialog.py`): formato riconosciuto
  dall'estensione del file, anteprima degli abbinamenti punto-US prima di
  scrivere, nessuna scrittura su Matrice senza conferma esplicita
  (`core/gps_import.py`)

## Menu "Progettazione" (toolbar, 2026-10-12/13)

Abilitato solo mentre e' attivo in QGIS uno dei layer del flusso
Progettazione (`CHIAVI_LAYER_PROGETTAZIONE` in `matrice_plugin.py`):

- **"Trincea..."/"Saggio..."**: rettangolo con larghezza/lunghezza date,
  posizionato con un click (`core/rettangolo_progettazione.py`), poi
  ruotabile con "Ruota elemento". Tabelle `trincea_progettazione`/
  `saggio_progettazione`.
- **"Unita' di Ricognizione (UR)..."**: poligono libero, editing nativo
  QGIS (nessun tool custom, a differenza di trincea/saggio). Tabella
  `survey_ur` (colonna `geom`, vista `v_survey_ur_qgis`).
- **"Rinvenimento..."**: dialog (sceglie la UR di appartenenza --
  obbligatoria) + un click per posizionare il punto
  (`core/ritrovamento_progettazione.py`). Tabella `survey_ritrovamento`,
  vista `v_survey_ritrovamento_qgis` con trigger `INSTEAD OF INSERT`
  dedicato che traduce la geometria in `lat`/`lng`.
- **"Crea buffer area di indagine..."**: chiede una distanza (metri) ad
  ogni generazione, calcola un buffer attorno all'area di cantiere
  (riproiezione locale azimutale equidistante) e lo salva su
  `cantiere.area_buffer_geom_wkt` -- guida visiva per disegnare le UR,
  nessun vincolo geometrico imposto. Layer di sola lettura, rigenerato ad
  ogni apertura cantiere, rimosso al cambio cantiere
  (`core/localizzazione_manager.py` `layer_buffer`).
- **"Ruota elemento"**: richiama lo strumento nativo QGIS "Ruota
  elementi" (nessuna reimplementazione).

All'apertura di un cantiere, il CRS di progetto QGIS si imposta sul
sistema di riferimento personale dell'utente loggato
(`core/sistema_riferimento.py`, mirror di `SISTEMI_RIFERIMENTO` in
`app/js/schema.js`) -- solo visualizzazione/digitazione, l'archiviazione
resta sempre WGS84/4326.

## Cosa resta da completare (segnato con `# TODO` nel codice)

1. **`core/output_generator.py`**: `_add_map_item` e
   `_add_ingombro_e_elenco` sono stub -- collegare l'API `QgsLayoutItemMap`
   / `QgsLayoutItemHtml` reale (Iterazione 3-4 della roadmap): rilievo
   complessivo per fasi, rilievo per singola fase, rilievo 1:20. Deciso di
   rimandarlo (layout cartografici) a una fase successiva.
2. **Stili QGIS (.qml)**: fasi/US/USM/sepolture -- vedi "Caratterizzazione
   grafica" sopra: US/Sepolture/USM sono a posto, resta solo da valutare se
   servira' anche un .qml dedicato per le fasi (oggi colori casuali
   generati dal plugin, nessun file fornito).

## Interfaccia di configurazione connessione (sezione 2/10)

Non si imposta piu' nulla da Console Python di QGIS: c'e' una schermata
dedicata (`ui/connection_settings_dialog.py`, menu Matrice -> "Impostazioni
connessione..."), divisa in due parti per non confondere l'operatore:

- **"Accesso a Matrice (login)"** -- URL Supabase e chiave pubblica, gia'
  precompilati con i valori di produzione e disabilitati per default: la
  maggior parte degli operatori non deve toccare nulla qui. Si abilitano
  solo spuntando "Usare un indirizzo diverso da quello predefinito", per
  chi deve puntare a un ambiente di sviluppo/test.
- **"Connessione dati mappa"** -- host, porta, nome database, utente e
  password **tecnici** (non quelli personali dell'operatore, che restano
  solo al login) usati per caricare i layer PostGIS in QGIS, con un
  pulsante **"Testa connessione"** che verifica subito se i dati inseriti
  funzionano -- in linguaggio semplice, mai un errore tecnico grezzo di
  psycopg2 -- prima di salvarli.

Se l'operatore clicca "Apri pannello Matrice" e la connessione dati mappa
non e' ancora configurata, questa schermata si apre automaticamente prima
del login: non e' possibile arrivare a un errore di connessione senza
sapere cosa impostare. I dati inseriti qui sono salvati sugli stessi
`QgsSettings` letti da `core/db_connector.py` (gruppo
`matrice_plugin/connection`) e `core/supabase_config.py` (gruppo
`matrice_plugin/supabase`), quindi non serve nessuna altra configurazione.

## Dati per il primo collaudo in QGIS

Per un test end-to-end serve solo compilare la schermata "Impostazioni
connessione" (sezione precedente) -- l'accesso Supabase e' gia'
precompilato di default, non va toccato:

- **Connessione dati mappa** (sezione "Connessione dati mappa" del
  dialogo): un ruolo tecnico dedicato e' stato creato apposta per questo
  collaudo, con permessi limitati alle sole tabelle/viste che il plugin
  usa (nessun accesso a dati anagrafici, fatturazione, ecc.):
  - Indirizzo del server: `db.wsbazealycuhemwqbplx.supabase.co`
  - Porta: `5432`
  - Nome database: `postgres`
  - Utente tecnico: `qgis_rilievo`
  - Password: comunicata a parte in chat (non salvata in questo file ne'
    nello zip) -- da cambiare con `ALTER ROLE qgis_rilievo WITH PASSWORD
    '...'` se si preferisce sceglierne una propria.
  - SSL: `require`
- **Login** (email/password personali, sulla schermata "Accedi a
  Matrice", non su questa): `t.saccone85@gmail.com` con la password gia'
  in uso per l'app web di Matrice -- e' lo stesso utente Supabase Auth,
  non un account separato per il plugin.

## Installazione per test

1. Copiare l'intera cartella `matrice_qgis_plugin/` in
   `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` (Linux/Mac)
   o `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` (Windows).
2. Aggiungere un'icona in `resources/icons/matrice.png`.
3. Creare `core/supabase_config.py` (non incluso nel repository di proposito,
   vedi `core/supabase_config.py.example`).
4. Installare la dipendenza mancante (`psycopg2`) PRIMA di attivare il
   plugin. Due modi, stesso risultato:
   - **Windows, un doppio clic (consigliato per chi non e' sviluppatore):**
     eseguire `scripts/installa_dipendenze_windows.bat`. Cerca da solo
     l'installazione di QGIS sul PC (sia il pacchetto standalone da
     qgis.org, sia OSGeo4W) e lancia il pip del Python usato DA QGIS.
     Non e' firmato digitalmente: Windows SmartScreen puo' avvisare al
     primo avvio ("Ulteriori informazioni" -> "Esegui comunque"). Se non
     trova QGIS automaticamente, lo dice e propone il metodo manuale.
   - **Manuale (qualunque sistema operativo):** da QGIS, Plugin -> Console
     Python, poi:
     ```python
     exec(open(r"PERCORSO/matrice_qgis_plugin/scripts/install_dependencies.py").read())
     ```
     (sostituire PERCORSO con il percorso reale della cartella del plugin).
     Eseguire da dentro QGIS, non con un Python esterno: su Windows in
     particolare, se ne possono trovare installati diversi sulla stessa
     macchina, e psycopg2 deve finire in quello usato DA QGIS.

   In entrambi i casi, riavviare QGIS al termine.
5. In QGIS: Estensioni -> Gestione estensioni -> Installate -> attivare
   "Matrice - Rilievo di Scavo".
6. Alla prima apertura del pannello Matrice, la schermata di configurazione
   connessione si apre da sola: inserire indirizzo del server, database,
   utente e password, verificare con "Testa connessione" e salvare (vedi
   "Interfaccia di configurazione connessione" sopra).
