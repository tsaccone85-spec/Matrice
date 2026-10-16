"""
Letture "critiche" di Matrice -- quelle che decidono cosa un operatore puo'
vedere (cantieri abilitati, elenco fasi, elenco US) -- instradate tramite
PostgREST invece che con una query diretta sul database (sezione 1/2 della
specifica: "Decisione presa: si procede con il servizio intermedio").

Ogni richiesta porta il token dell'utente loggato: e' il database stesso,
tramite le policy di Row Level Security gia' presenti in Matrice
(has_cantiere_access, has_cantiere_access_editor), a decidere quali righe
restituire -- lo stesso meccanismo di controllo gia' usato dall'app web,
non una logica di permessi duplicata nel plugin.

La connessione PostgreSQL diretta (core/db_connector.py) resta invece
riservata al solo caricamento dei layer PostGIS in QGIS e alla scrittura
delle geometrie (vedi nota in db_connector.py): e' un limite tecnico del
provider "postgres" di QGIS, che deve parlare direttamente con il database
per poter editare una geometria in mappa.
"""


# Valori ammessi da cantiere.tipo_intervento (check constraint su Supabase)
# con l'etichetta mostrata nel menu a tendina "Nuovo cantiere". L'ordine qui
# e' l'ordine di visualizzazione.
TIPI_INTERVENTO = [
    ("scavo", "Scavo"),
    ("sorveglianza", "Sorveglianza archeologica"),
    ("vpa", "Verifica preventiva dell'interesse archeologico (VPA)"),
    ("carotaggi", "Carotaggi"),
    ("progettazione", "Progettazione archeologica"),
    ("museo", "Museo"),
]

# Tipi di lavoro gated da una funzione specifica in funzione_accesso_utente
# (stessa tabella/logica usata per le altre funzioni a pagamento, es.
# relazione_ia): un tipo assente da questa mappa (scavo, carotaggi,
# progettazione, museo) e' una funzionalita' di base, sempre proponibile.
FUNZIONE_RICHIESTA_PER_TIPO_INTERVENTO = {
    "sorveglianza": "sorveglianza_trincea",
    "vpa": "vpa_gna",
}


class MatriceApi:
    """
    Riceve il SessionManager (non un client gia' pronto) e richiede il
    client REST a ogni chiamata tramite session.rest_client(): cosi', se
    l'operatore aggiorna URL/chiave Supabase da
    ui/connection_settings_dialog.py, le letture successive usano subito i
    valori nuovi senza bisogno di ricreare MatriceApi.
    """

    def __init__(self, session):
        self.session = session

    @property
    def rest(self):
        return self.session.rest_client()

    def list_cantieri_abilitati(self, access_token):
        """
        Cantieri a cui l'utente loggato ha accesso. La riga viene restituita
        solo se la Row Level Security su `cantiere` (has_cantiere_access) la
        consente per questo utente -- non serve rifiltrare lato plugin.

        Si legge direttamente `cantiere`, non piu' un reverse-join da
        `cantiere_accesso` (bug corretto: cosi' facendo, un cantiere senza
        NESSUNA riga in cantiere_accesso restava invisibile anche per un
        utente "referente", che per regola vede automaticamente tutti i
        cantieri della propria azienda -- has_cantiere_access lo permette,
        ma non c'era alcuna riga da cui farlo risalire con quella query).
        """
        rows = self.rest.select(
            "cantiere", access_token, params={"select": "id,nome_cantiere"},
        )
        cantieri = [{"id": row["id"], "nome_cantiere": row["nome_cantiere"]} for row in (rows or [])]
        cantieri.sort(key=lambda c: (c["nome_cantiere"] or "").lower())
        return cantieri

    def trova_utente_per_email(self, email, access_token):
        """
        Utente con questa email, filtrato dalla RLS su `utente` (vede solo la
        propria azienda -- stesso vincolo della query web in dashboard.js
        `nuovoCantiere`): None se non trovato, cosi' il chiamante decide se
        trattarlo come "nessun referente diverso dal loggato".
        """
        rows = self.rest.select(
            "utente", access_token,
            params={"email": f"eq.{email.strip().lower()}", "select": "id,email"},
        )
        return rows[0] if rows else None

    def azienda_utente_loggato(self, access_token):
        """azienda_id dell'utente loggato -- richiesto per crea_cantiere, 'cantiere.azienda_id' e' NOT NULL senza default."""
        rows = self.rest.select("utente", access_token, params={"id": f"eq.{self.session.user_id}", "select": "azienda_id"})
        if not rows:
            raise RuntimeError("Impossibile determinare l'azienda dell'utente loggato.")
        return rows[0]["azienda_id"]

    def sistema_riferimento_utente_loggato(self, access_token):
        """id (SISTEMI_RIFERIMENTO) del sistema di riferimento personale dell'utente loggato."""
        rows = self.rest.select("utente", access_token, params={"id": f"eq.{self.session.user_id}", "select": "sistema_riferimento_id"})
        return rows[0]["sistema_riferimento_id"] if rows else 1

    def tipi_intervento_consentiti(self, access_token):
        """
        Sottoinsieme di TIPI_INTERVENTO che l'utente loggato puo' scegliere
        nel dialog "Nuovo cantiere": i tipi di base (vedi
        FUNZIONE_RICHIESTA_PER_TIPO_INTERVENTO) sono sempre proposti, gli
        altri solo se all'utente e' stata concessa la funzione
        corrispondente -- stessa tabella/meccanismo delle altre funzioni a
        pagamento (es. relazione_ia), non una logica di permessi nuova.
        """
        rows = self.rest.select(
            "funzione_accesso_utente", access_token,
            params={"utente_id": f"eq.{self.session.user_id}", "select": "funzione"},
        )
        funzioni_concesse = {row["funzione"] for row in (rows or [])}
        return [
            (valore, etichetta)
            for valore, etichetta in TIPI_INTERVENTO
            if FUNZIONE_RICHIESTA_PER_TIPO_INTERVENTO.get(valore) in (None, *funzioni_concesse)
        ]

    def crea_cantiere(self, campi, access_token):
        """
        Crea un cantiere su Matrice (sezione 4, "Crea cantiere da QGIS").
        `campi` e' il dizionario di colonne gia' pronto (nome_cantiere,
        tipo_intervento, comune, provincia, regione, anno_esecuzione,
        referente_campo_utente_id, azienda_id, created_by, ...): la
        validazione dei campi obbligatori resta nel dialog, qui si inserisce
        senza altra logica -- la RLS su `cantiere` decide se e' permesso.
        """
        return self.rest.insert("cantiere", campi, access_token)

    def get_regione_cantiere(self, cantiere_id, access_token):
        """Regione del cantiere (sfondo CTR regionale, core/basemap_manager.py), o None se non impostata."""
        rows = self.rest.select(
            "cantiere", access_token, params={"id": f"eq.{cantiere_id}", "select": "regione"}
        )
        return rows[0]["regione"] if rows else None

    def get_localizzazione_cantiere(self, cantiere_id, access_token):
        """
        Punto di localizzazione dell'intervento (lat/lng) ed eventuale area
        gia' disegnata (area_geom_wkt, testo WKT -- Matrice non ha una vera
        colonna geometry per il cantiere, vedi core/localizzazione_manager.py).
        """
        rows = self.rest.select(
            "cantiere", access_token,
            params={"id": f"eq.{cantiere_id}", "select": "lat,lng,area_geom_wkt,area_buffer_geom_wkt"},
        )
        return rows[0] if rows else None

    def update_area_intervento(self, cantiere_id, wkt, access_token):
        """
        Scrive l'area disegnata in QGIS su cantiere.area_geom_wkt (campo di
        solo testo). La policy RLS di scrittura su cantiere richiede il
        ruolo "referente" sulla stessa azienda: un operatore senza quel
        ruolo ricevera' un errore di permessi chiaro, non e' un bug del
        plugin.
        """
        return self.rest.update("cantiere", "id", cantiere_id, {"area_geom_wkt": wkt}, access_token)

    def update_area_buffer(self, cantiere_id, wkt, access_token):
        """Scrive il buffer generato attorno all'area di indagine su cantiere.area_buffer_geom_wkt."""
        return self.rest.update("cantiere", "id", cantiere_id, {"area_buffer_geom_wkt": wkt}, access_token)

    def list_fasi(self, cantiere_id, access_token):
        """
        NOTA: come in db_connector.py, `fase` e' un campo testuale libero
        sulla scheda US, non una tabella a se'. PostgREST non offre un
        DISTINCT diretto in una singola richiesta: si dedupplica qui, con
        volumi (poche decine di fasi per cantiere) che non giustificano
        una vista dedicata solo per questo.
        """
        rows = self.rest.select(
            "us",
            access_token,
            params={
                "select": "fase",
                "cantiere_id": f"eq.{cantiere_id}",
                "fase": "not.is.null",
            },
        )
        fasi_uniche = sorted({row["fase"] for row in (rows or []) if row.get("fase")})
        return [{"fase": fase} for fase in fasi_uniche]

    def list_us(self, cantiere_id, access_token, solo_attive=True):
        """
        Elenco leggero della US (non la scheda completa), con l'eventuale
        sepoltura collegata tramite sepoltura_us -- stesso contenuto della
        versione precedente basata su SQL diretto, ora filtrato dalla Row
        Level Security invece che da un WHERE cantiere_id.
        """
        params = {
            "select": "id,numero_us,definizione,fase,attiva,sepoltura_us(sepoltura_id)",
            "cantiere_id": f"eq.{cantiere_id}",
            "order": "numero_us",
        }
        if solo_attive:
            params["attiva"] = "eq.true"

        rows = self.rest.select("us", access_token, params=params)
        risultato = []
        for row in rows or []:
            sepolture_collegate = row.get("sepoltura_us") or []
            sepoltura_id = sepolture_collegate[0]["sepoltura_id"] if sepolture_collegate else None
            risultato.append(
                {
                    "id": row["id"],
                    "numero_us": row["numero_us"],
                    "definizione": row["definizione"],
                    "fase": row["fase"],
                    "attiva": row["attiva"],
                    "sepoltura_id": sepoltura_id,
                }
            )
        return risultato

    # ------------------------------------------------------------------
    # Scheda US completa (doppio click su un elemento selezionato in
    # mappa): lettura e scrittura dei campi descrittivi, sempre via
    # PostgREST con il token dell'utente -- la Row Level Security
    # (has_cantiere_access/has_cantiere_access_editor, le stesse di tutto
    # il resto del plugin) decide se la riga e' leggibile/scrivibile.
    # ------------------------------------------------------------------
    def get_us(self, us_id, access_token):
        rows = self.rest.select("us", access_token, params={"id": f"eq.{us_id}"})
        return rows[0] if rows else None

    def update_us(self, us_id, campi, access_token):
        return self.rest.update("us", "id", us_id, campi, access_token)

    def list_reperti(self, us_id, access_token):
        return self.rest.select(
            "reperto",
            access_token,
            params={"us_id": f"eq.{us_id}", "order": "numero_reperto"},
        ) or []

    def update_reperto(self, reperto_id, campi, access_token):
        return self.rest.update("reperto", "id", reperto_id, campi, access_token)

    def get_sepoltura(self, sepoltura_id, access_token):
        rows = self.rest.select("sepoltura", access_token, params={"id": f"eq.{sepoltura_id}"})
        return rows[0] if rows else None

    def update_sepoltura(self, sepoltura_id, campi, access_token):
        return self.rest.update("sepoltura", "id", sepoltura_id, campi, access_token)

    # ------------------------------------------------------------------
    # Elenchi codice -> id per l'import GPS (core/gps_import.py): colonne
    # stratigrafiche e carotaggi NON hanno nessun legame con una US, vanno
    # abbinati al proprio numero (numero_colonna/numero_carotaggio), non al
    # numero US come il percorso storico (vedi gps_import._codici_cantiere).
    # ------------------------------------------------------------------
    def list_colonne_stratigrafiche(self, cantiere_id, access_token):
        return self.rest.select(
            "colonna_stratigrafica",
            access_token,
            params={"select": "id,numero_colonna", "cantiere_id": f"eq.{cantiere_id}"},
        ) or []

    def list_carotaggi(self, cantiere_id, access_token):
        return self.rest.select(
            "carotaggio",
            access_token,
            params={"select": "id,numero_carotaggio", "cantiere_id": f"eq.{cantiere_id}"},
        ) or []

    def list_sezioni(self, cantiere_id, access_token):
        return self.rest.select(
            "sezione_prospetto",
            access_token,
            params={"select": "id,numero_sezione", "cantiere_id": f"eq.{cantiere_id}"},
        ) or []

    # ------------------------------------------------------------------
    # Scheda colonna stratigrafica / carotaggio completa
    # (ui/punto_dettaglio_dialog.py, doppio click su un elemento
    # selezionato): niente legame con una US, a differenza di get_us.
    # ------------------------------------------------------------------
    def get_colonna_stratigrafica(self, colonna_id, access_token):
        rows = self.rest.select("colonna_stratigrafica", access_token, params={"id": f"eq.{colonna_id}"})
        return rows[0] if rows else None

    def update_colonna_stratigrafica(self, colonna_id, campi, access_token):
        return self.rest.update("colonna_stratigrafica", "id", colonna_id, campi, access_token)

    def get_carotaggio(self, carotaggio_id, access_token):
        rows = self.rest.select("carotaggio", access_token, params={"id": f"eq.{carotaggio_id}"})
        return rows[0] if rows else None

    def update_carotaggio(self, carotaggio_id, campi, access_token):
        return self.rest.update("carotaggio", "id", carotaggio_id, campi, access_token)

    # ------------------------------------------------------------------
    # Storage: le colonne foto_*_url/file_url (reperto, foto_scavo,
    # fotogrammetria, carotaggio, colonna_stratigrafica) NON sono URL
    # completi, sono percorsi relativi dentro il bucket privato
    # "foto-scavo" (verificato direttamente su Supabase) -- va scaricata
    # con il token dell'utente, un link diretto non funziona su un bucket
    # privato.
    # ------------------------------------------------------------------
    FOTO_BUCKET = "foto-scavo"

    def scarica_foto(self, percorso_relativo, access_token):
        """Ritorna (dati, None) se riuscito, o (None, messaggio_errore) altrimenti."""
        if not percorso_relativo:
            return None, None
        return self.rest.download_object(self.FOTO_BUCKET, percorso_relativo, access_token)

    def list_foto_scavo(self, us_id, access_token):
        return self.rest.select(
            "foto_scavo",
            access_token,
            params={"us_id": f"eq.{us_id}", "order": "numero_progressivo"},
        ) or []
