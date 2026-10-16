"""
Schermata di configurazione della connessione a Matrice (sezione 2/10).

Due sezioni distinte, per non confondere l'operatore:

1. "Accesso a Matrice (login)" -- URL Supabase e chiave pubblica usati da
   Supabase Auth/PostgREST (core/auth.py, core/matrice_api.py). Sono
   precompilati con i valori di produzione (core/supabase_config.py) e
   disabilitati per default: la maggior parte degli operatori non deve
   toccarli. Si abilitano solo spuntando la casella "ambiente diverso",
   per chi deve puntare a un ambiente di sviluppo/test.

2. "Connessione dati mappa" -- host/porta/database/utente tecnico usati
   dalla connessione PostgreSQL diretta (core/db_connector.py), riservata
   al caricamento dei layer PostGIS in QGIS. Non sono le credenziali
   personali dell'operatore (quelle si inseriscono al login): sono un
   utente database condiviso, a permessi limitati, impostato una volta
   sola da chi gestisce Matrice.

Salva sugli stessi QSettings letti da core/db_connector.py e
core/supabase_config.py, quindi non serve nessuna modifica altrove: una
volta salvati qui, il resto del plugin li usa automaticamente.
"""

import os

from qgis.core import QgsSettings
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog

from ..core.db_connector import SETTINGS_GROUP
from ..core.supabase_config import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL, SUPABASE_SETTINGS_GROUP

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), "connection_settings_dialog.ui"))

DEFAULT_PORT = "5432"
# Supabase chiama sempre "postgres" il database di un progetto (verificato
# in diretta sul progetto di produzione con `select current_database()`):
# non esiste un database chiamato "matrice" da nessuna parte, era un
# placeholder sbagliato.
DEFAULT_DBNAME = "postgres"
DEFAULT_SSLMODE = "require"


class ConnectionSettingsDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.statusLabel.setText("")
        self.testButton.clicked.connect(self._on_test_connection)
        self.supabaseAdvancedCheckBox.toggled.connect(self._on_supabase_advanced_toggled)
        # Il file .ui e' scritto a mano (non generato da Qt Designer), quindi
        # il buttonBox non chiude da solo la finestra: senza queste due righe
        # "Salva"/"Annulla" restano senza effetto (il bug appena trovato).
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        self._load_from_settings()

    # ------------------------------------------------------------------
    def _load_from_settings(self):
        settings = QgsSettings()

        settings.beginGroup(SUPABASE_SETTINGS_GROUP)
        supabase_url = settings.value("url", "") or ""
        supabase_anon_key = settings.value("anon_key", "") or ""
        settings.endGroup()

        usa_valori_personalizzati = bool(supabase_url) and supabase_url != DEFAULT_SUPABASE_URL
        self.supabaseUrlEdit.setText(supabase_url or DEFAULT_SUPABASE_URL)
        self.supabaseAnonKeyEdit.setText(supabase_anon_key or DEFAULT_SUPABASE_ANON_KEY)
        self.supabaseAdvancedCheckBox.setChecked(usa_valori_personalizzati)
        self._on_supabase_advanced_toggled(usa_valori_personalizzati)

        settings.beginGroup(SETTINGS_GROUP)
        self.hostEdit.setText(settings.value("host", ""))
        self.portEdit.setText(str(settings.value("port", DEFAULT_PORT)))
        self.dbnameEdit.setText(settings.value("dbname", DEFAULT_DBNAME))
        self.dbUserEdit.setText(settings.value("db_user", ""))
        self.dbPasswordEdit.setText(settings.value("db_password", ""))
        sslmode = settings.value("sslmode", DEFAULT_SSLMODE)
        settings.endGroup()

        index = self.sslmodeCombo.findText(sslmode)
        if index >= 0:
            self.sslmodeCombo.setCurrentIndex(index)

    def _on_supabase_advanced_toggled(self, checked):
        self.supabaseUrlEdit.setEnabled(checked)
        self.supabaseAnonKeyEdit.setEnabled(checked)
        if not checked:
            self.supabaseUrlEdit.setText(DEFAULT_SUPABASE_URL)
            self.supabaseAnonKeyEdit.setText(DEFAULT_SUPABASE_ANON_KEY)

    def save_to_settings(self):
        """Ritorna (True, None) se salvato, (False, messaggio) se mancano dati obbligatori."""
        params = self._current_params()
        if not params["host"] or not params["dbname"] or not params["user"]:
            return False, "Indirizzo del server, nome database e utente tecnico sono obbligatori."

        settings = QgsSettings()

        settings.beginGroup(SUPABASE_SETTINGS_GROUP)
        if self.supabaseAdvancedCheckBox.isChecked():
            settings.setValue("url", self.supabaseUrlEdit.text().strip() or DEFAULT_SUPABASE_URL)
            settings.setValue("anon_key", self.supabaseAnonKeyEdit.text().strip() or DEFAULT_SUPABASE_ANON_KEY)
        else:
            # Torna ai valori di produzione: nessun override personalizzato salvato.
            settings.remove("url")
            settings.remove("anon_key")
        settings.endGroup()

        settings.beginGroup(SETTINGS_GROUP)
        settings.setValue("host", params["host"])
        settings.setValue("port", params["port"])
        settings.setValue("dbname", params["dbname"])
        settings.setValue("db_user", params["user"])
        settings.setValue("db_password", params["password"])
        settings.setValue("sslmode", params["sslmode"])
        settings.endGroup()
        return True, None

    # ------------------------------------------------------------------
    def _current_params(self):
        return {
            "host": self.hostEdit.text().strip(),
            "port": self.portEdit.text().strip() or DEFAULT_PORT,
            "dbname": self.dbnameEdit.text().strip() or DEFAULT_DBNAME,
            "user": self.dbUserEdit.text().strip(),
            "password": self.dbPasswordEdit.text(),
            "sslmode": self.sslmodeCombo.currentText(),
        }

    def _on_test_connection(self):
        params = self._current_params()
        if not params["host"] or not params["dbname"] or not params["user"]:
            self._set_status("Indirizzo del server, nome database e utente tecnico sono obbligatori.", ok=False)
            return

        self.testButton.setEnabled(False)
        self._set_status("Verifica in corso...", ok=None)
        try:
            self._try_connect(params)
        except Exception as exc:  # qualunque errore psycopg2/di rete
            self._set_status(self._messaggio_semplice(exc), ok=False)
        else:
            self._set_status("Connessione riuscita: i dati inseriti sono corretti.", ok=True)
        finally:
            self.testButton.setEnabled(True)

    def _try_connect(self, params):
        import psycopg2

        conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            dbname=params["dbname"],
            user=params["user"],
            password=params["password"],
            sslmode=params["sslmode"],
            connect_timeout=8,
        )
        conn.close()

    def _messaggio_semplice(self, exc):
        """
        Mai un errore tecnico grezzo di psycopg2 mostrato all'operatore
        (principio generale del plugin -- sezione 2/8 della specifica):
        qui distinguiamo solo i casi piu' comuni.
        """
        testo = str(exc).lower()
        if "password" in testo or "autentic" in testo:
            return "Utente o password tecnici non corretti."
        if "could not translate host" in testo or "nodename" in testo or "name or service not known" in testo:
            return "Indirizzo del server non raggiungibile: controllare che sia scritto correttamente."
        if "timeout" in testo or "timed out" in testo:
            return "Il server non risponde: controllare la connessione a internet o l'indirizzo inserito."
        if "does not exist" in testo and "database" in testo:
            return "Il nome del database inserito non esiste su questo server."
        if "refused" in testo:
            return "Connessione rifiutata dal server: controllare indirizzo e porta."
        return "Impossibile collegarsi con i dati inseriti. Verificare indirizzo, porta, database, utente e password."

    def _set_status(self, message, ok):
        color = "#1b7f3a" if ok is True else ("#5f6368" if ok is None else "#b3261e")
        self.statusLabel.setStyleSheet(f"color: {color};")
        self.statusLabel.setText(message)
