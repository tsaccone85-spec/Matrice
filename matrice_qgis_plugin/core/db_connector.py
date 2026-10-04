"""
Connessione PostgreSQL/PostGIS diretta a Matrice (sezione 1 e 5 della
specifica) -- riservata a cio' che il provider "postgres" di QGIS richiede
davvero come connessione diretta al database:

1. caricare i layer PostGIS nativi in mappa (core/layer_manager.py);
2. scrivere le geometrie modificate e la riga di log in
   us_storico_modifiche ad ogni commit di editing
   (core/sync_handler.py, core/point_position_sync.py, core/gps_import.py).

Le credenziali usate qui sono quelle **tecniche** dell'installazione (un
utente database condiviso, a permessi limitati, impostato una sola volta
da chi gestisce Matrice -- vedi ui/connection_settings_dialog.py), non le
credenziali personali dell'operatore: quelle passano da Supabase Auth
(core/auth.py). Le letture che dipendono da chi e' loggato (cantieri
abilitati, fasi, elenco US) NON passano da qui: sono in
core/matrice_api.py, tramite PostgREST con il token dell'utente, secondo
la "Decisione presa" di sezione 1 ("si procede con il servizio
intermedio, non con la connessione diretta").
"""

import psycopg2
import psycopg2.extras

from qgis.core import QgsSettings

SETTINGS_GROUP = "matrice_plugin/connection"


class MatriceConnection:
    """Wrapper sottile su psycopg2, con connessione riutilizzata per sessione."""

    def __init__(self, session):
        self.session = session
        self._conn = None

    # ------------------------------------------------------------------
    # Connessione
    # ------------------------------------------------------------------
    def _connection_params(self):
        settings = QgsSettings()
        settings.beginGroup(SETTINGS_GROUP)
        params = {
            "host": settings.value("host", ""),
            "port": settings.value("port", "5432"),
            "dbname": settings.value("dbname", "matrice"),
            "user": settings.value("db_user", ""),
            "password": settings.value("db_password", ""),
            "sslmode": settings.value("sslmode", "require"),
        }
        settings.endGroup()
        return params

    @staticmethod
    def is_configured():
        """
        True se host/database/utente sono gia' stati impostati (dalla
        schermata ui/connection_settings_dialog.py). Usato per sapere se
        proporre quella schermata prima ancora del login (sezione 2):
        senza questi dati il login fallirebbe con un errore tecnico che
        l'operatore non saprebbe interpretare.
        """
        settings = QgsSettings()
        settings.beginGroup(SETTINGS_GROUP)
        host = settings.value("host", "")
        dbname = settings.value("dbname", "")
        user = settings.value("db_user", "")
        settings.endGroup()
        return bool(host) and bool(dbname) and bool(user)

    def _connect(self):
        if self._conn is None or self._conn.closed:
            params = self._connection_params()
            if not params["host"] or not params["user"]:
                raise ConnectionError(
                    "Connessione a Matrice non configurata: impostare indirizzo del server, database e utente."
                )
            try:
                self._conn = psycopg2.connect(**params)
            except psycopg2.OperationalError as exc:
                raise ConnectionError(str(exc)) from exc
        return self._conn

    def postgis_uri(self, table, geometry_column="geom", key_column="id", wkb_type=None, srid=None):
        """
        URI per caricare un layer PostGIS nativo in QGIS (sezione 4, passo 1).

        wkb_type/srid vanno sempre passati esplicitamente da layer_manager.py:
        senza, QGIS deve dedurli campionando i dati, il che fallisce sia
        quando il subset SQL (filtro cantiere_id) restituisce zero righe,
        sia quando la colonna contiene geometrie di tipo diverso tra loro
        (es. rilievo_geometria: poligoni, linee e punti nella stessa
        colonna `geom`, a seconda della categoria disegnata).
        """
        params = self._connection_params()
        from qgis.core import QgsDataSourceUri

        uri = QgsDataSourceUri()
        uri.setConnection(params["host"], str(params["port"]), params["dbname"], params["user"], params["password"])
        uri.setDataSource("public", table, geometry_column, "", key_column)
        if wkb_type is not None:
            uri.setWkbType(wkb_type)
        if srid is not None:
            uri.setSrid(str(srid))
        return uri

    # ------------------------------------------------------------------
    # Letture residue -- solo quelle strumentali alla scrittura diretta
    # (es. leggere il valore precedente di una geometria prima di un
    # update, per il log in us_storico_modifiche). Le letture che
    # dipendono dai permessi dell'utente sono in core/matrice_api.py.
    # ------------------------------------------------------------------
    def _fetch_all(self, sql, params):
        conn = self._connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def execute(self, sql, params):
        """Scrittura generica, usata da sync_handler per i commit (sezione 5)."""
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()

    def execute_returning(self, sql, params):
        """Come execute(), ma per una SELECT: ritorna le righe (lista di dict)."""
        return self._fetch_all(sql, params)
