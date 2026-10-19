"""
Autenticazione e gestione sessione (sezione 2 della specifica).

Il login usa Supabase Auth (GoTrue), lo stesso meccanismo gia' usato
dall'app web di Matrice: email e password dell'operatore, verificate da
Supabase, non da una query diretta del plugin sulla tabella `utente`. La
"Decisione presa" di sezione 1 ("si procede con il servizio intermedio,
non con la connessione diretta") e' soddisfatta da Supabase stesso: GoTrue
per il login, PostgREST con Row Level Security per le letture che dipendono
dai permessi dell'utente (core/matrice_api.py) -- senza dover costruire e
mantenere un microservizio dedicato solo per questo plugin.

Le credenziali, se l'operatore lo richiede ("Ricordami"), sono salvate nel
QGIS Authentication Database (gia' cifrato da QGIS stesso) invece che in
chiaro nel profilo utente: nessuna password su file di configurazione.
"""

import datetime

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from .supabase_client import SupabaseAuthError, SupabaseRestClient
from .supabase_config import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL, SUPABASE_SETTINGS_GROUP

AUTH_CONFIG_NAME = "matrice_plugin_credentials"
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600


class SessionManager:
    """Tiene lo stato di autenticazione per la sessione QGIS corrente."""

    def __init__(self):
        self.user_id = None
        self.username = None  # email usata per il login
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        self.cantiere_corrente_id = None  # impostato da handle_open_tavola (sezione 4, passo 1)
        self._rest_client = None

    def rest_client(self):
        """
        Client REST verso Supabase, condiviso con core/matrice_api.py per
        le letture filtrate per utente. Creato al primo utilizzo cosi' da
        leggere sempre l'URL/chiave correnti da QgsSettings (impostabili
        da ui/connection_settings_dialog.py per un ambiente diverso da
        quello di produzione).
        """
        if self._rest_client is None:
            settings = QgsSettings()
            settings.beginGroup(SUPABASE_SETTINGS_GROUP)
            url = settings.value("url", DEFAULT_SUPABASE_URL) or DEFAULT_SUPABASE_URL
            anon_key = settings.value("anon_key", DEFAULT_SUPABASE_ANON_KEY) or DEFAULT_SUPABASE_ANON_KEY
            settings.endGroup()
            self._rest_client = SupabaseRestClient(url, anon_key)
        return self._rest_client

    def reset_rest_client(self):
        """
        Da richiamare dopo un salvataggio in ui/connection_settings_dialog.py:
        forza rest_client() a rileggere URL/chiave aggiornati da QgsSettings
        invece di continuare a usare quelli letti alla prima chiamata.
        """
        self._rest_client = None

    def is_authenticated(self):
        if self.access_token is None or self.token_expiry is None:
            return False
        return datetime.datetime.now() < self.token_expiry

    def saved_credentials(self):
        """
        (username, password) salvati con "Ricordami", o (None, None) se non
        ce ne sono -- da usare per precompilare il form di login (bug
        corretto: la spunta "Ricordami" salvava le credenziali ma nessun
        punto del plugin le rileggeva mai, quindi all'apertura successiva
        l'operatore si ritrovava comunque i campi vuoti).
        """
        config = self._find_saved_config()
        if config is None:
            return None, None
        return config.config("username") or None, config.config("password") or None

    def login(self, username, password, remember=False):
        """
        Verifica le credenziali su Matrice (Supabase Auth) e apre una
        sessione.

        Ritorna (True, None) in caso di successo, oppure
        (False, "messaggio in linguaggio semplice") in caso di errore --
        mai un codice tecnico o un'eccezione grezza mostrata all'operatore.
        """
        if not username or not password:
            return False, "Inserire nome utente (email) e password."

        try:
            dati_sessione = self.rest_client().sign_in_with_password(username, password)
        except SupabaseAuthError:
            return False, "Nome utente o password non corretti."
        except ConnectionError:
            return False, "Impossibile raggiungere Matrice: verificare la connessione internet."
        except Exception:
            return False, "Si e' verificato un errore imprevisto durante l'accesso. Riprovare."

        utente = (dati_sessione or {}).get("user") or {}
        if not (dati_sessione or {}).get("access_token") or not utente.get("id"):
            return False, "Nome utente o password non corretti."

        self.user_id = utente["id"]  # uuid condiviso tra auth.users e utente (sezione 2)
        self.username = username
        self.access_token = dati_sessione["access_token"]
        self.refresh_token = dati_sessione.get("refresh_token")
        self._imposta_scadenza(dati_sessione.get("expires_in"))

        if remember:
            self._save_credentials(username, password)

        return True, None

    def logout(self):
        self.user_id = None
        self.username = None
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None

    def renew_if_needed(self):
        """
        Rinnovo trasparente del token durante l'uso attivo del plugin
        (sezione 2), tramite il refresh_token rilasciato da Supabase al
        login -- nessun nuovo login esplicito richiesto all'operatore
        finche' il refresh_token resta valido.
        """
        if not self.access_token or self.is_authenticated() or not self.refresh_token:
            return
        try:
            dati_sessione = self.rest_client().refresh_session(self.refresh_token)
        except Exception:
            # Rinnovo silenzioso fallito: si passa a sessione scaduta senza
            # bloccare il plugin. Alla prossima azione l'operatore vedra'
            # semplicemente richiesto un nuovo accesso.
            self.logout()
            return

        self.access_token = dati_sessione.get("access_token")
        self.refresh_token = dati_sessione.get("refresh_token", self.refresh_token)
        self._imposta_scadenza(dati_sessione.get("expires_in"))

    # ------------------------------------------------------------------
    # Dettagli implementativi
    # ------------------------------------------------------------------
    def _imposta_scadenza(self, expires_in_seconds):
        secondi = expires_in_seconds or DEFAULT_TOKEN_LIFETIME_SECONDS
        self.token_expiry = datetime.datetime.now() + datetime.timedelta(seconds=secondi)

    def _save_credentials(self, username, password):
        auth_manager = QgsApplication.authManager()
        # Una sola config: prima ne veniva creata una nuova a ogni login con
        # "Ricordami", e _find_saved_config le caricava tutte (login sempre
        # piu' lento). Si riusa l'id esistente e si eliminano i duplicati.
        esistenti = self._configs_salvate()
        config = QgsAuthMethodConfig("Basic")
        if esistenti:
            config.setId(esistenti[0].id())
            for duplicato in esistenti[1:]:
                auth_manager.removeAuthenticationConfig(duplicato.id())
        config.setName(AUTH_CONFIG_NAME)
        config.setConfig("username", username)
        config.setConfig("password", password)
        if esistenti:
            # id gia' presente: storeAuthenticationConfig lo rifiuta ("ID di
            # configurazione non unico"), si aggiorna la config esistente.
            auth_manager.updateAuthenticationConfig(config)
        else:
            auth_manager.storeAuthenticationConfig(config)

    def _configs_salvate(self):
        auth_manager = QgsApplication.authManager()
        trovate = []
        # configIds() non da' i nomi: availableAuthMethodConfigs() si', in una
        # sola chiamata e senza decifrare le password.
        for config_id, info in auth_manager.availableAuthMethodConfigs().items():
            if info.name() == AUTH_CONFIG_NAME:
                config = QgsAuthMethodConfig()
                auth_manager.loadAuthenticationConfig(config_id, config, True)
                trovate.append(config)
        return trovate

    def _find_saved_config(self):
        trovate = self._configs_salvate()
        return trovate[0] if trovate else None
