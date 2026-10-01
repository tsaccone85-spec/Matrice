"""
Classe principale del plugin: registra il pannello (dock widget) in QGIS,
gestisce il ciclo di vita (initGui/unload) e fa da ponte tra l'interfaccia
utente e i moduli di core/.

Riferimento: sezione 10 della specifica tecnica ("Struttura tecnica del
plugin PyQGIS").
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from .core.auth import SessionManager
from .core.basemap_manager import BasemapManager
from .core.db_connector import MatriceConnection
from .core.dem_sampler import DemSampler
from .core.gps_import import GpsImporter
from .core.layer_manager import LAYER_SOURCES, LayerManager
from .core.matrice_api import MatriceApi
from .core.point_position_sync import PointPositionSyncHandler
from .core.sync_handler import SyncHandler
from .core.style_manager import StyleManager
from .core.quick_views import QuickViewManager
from .ui.main_dock_widget import MainDockWidget
from .ui.login_dialog import LoginDialog
from .ui.connection_settings_dialog import ConnectionSettingsDialog
from .ui.gps_import_dialog import GpsImportDialog

PLUGIN_DIR = os.path.dirname(__file__)


class MatricePlugin:
    """Punto di ingresso registrato da QGIS tramite classFactory()."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.dock_widget = None
        self.toolbar = None

        # Stato applicativo condiviso tra i moduli: nessuna copia locale
        # definitiva del dato, solo lo stato della sessione corrente.
        self.session = SessionManager()
        self.connection = MatriceConnection(self.session)
        # Letture che dipendono da chi e' loggato (cantieri abilitati, fasi,
        # elenco US): via PostgREST con il token dell'utente, non con la
        # connessione diretta (sezione 1 -- "servizio intermedio").
        self.matrice_api = MatriceApi(self.session)
        self.layer_manager = LayerManager(self.connection)
        self.basemap_manager = BasemapManager()
        self.style_manager = StyleManager()
        self.quick_view_manager = QuickViewManager(
            config_path=os.path.join(PLUGIN_DIR, "config", "quick_views.json"),
            style_manager=self.style_manager,
            styles_dir=os.path.join(PLUGIN_DIR, "config", "styles"),
        )
        self.sync_handler = SyncHandler(self.connection, self.session)
        # Import GPS differenziale (sezione 4, passo 3): scrittura via
        # connessione tecnica (apply_matches), lettura elenco US via
        # PostgREST (self.matrice_api), coerente col resto del plugin.
        self.gps_importer = GpsImporter(self.connection, self.matrice_api, self.session)
        # Elenco fasi dell'ultimo cantiere aperto, per assegnare colori
        # casuali distinti alla vista rapida "Vista per fasi" (sezione 6)
        # senza dover rileggere i valori dal layer ogni volta.
        self._fasi_correnti = []

        # Sincronizzazione dedicata ai layer puntuali con lat/lng diretti
        # (carotaggio, colonna_stratigrafica, sezione_prospetto): regola
        # "quota manuale vince, altrimenti ricalcolo dal DEM" (sezione 5/8).
        self.dem_sampler = DemSampler()
        self.point_sync_handler = PointPositionSyncHandler(
            self.connection, self.dem_sampler, self.session, on_warning=self._show_warning
        )

    # ------------------------------------------------------------------
    # Ciclo di vita QGIS
    # ------------------------------------------------------------------
    def initGui(self):
        """Chiamato da QGIS all'avvio: crea toolbar, azione e dock widget."""
        icon_path = os.path.join(PLUGIN_DIR, "resources", "icons", "matrice.png")
        self.toolbar = self.iface.addToolBar("Matrice")
        self.toolbar.setObjectName("MatriceToolbar")

        action = QAction(QIcon(icon_path), "Apri pannello Matrice", self.iface.mainWindow())
        action.triggered.connect(self.toggle_dock)
        self.toolbar.addAction(action)
        self.iface.addPluginToMenu("&Matrice", action)
        self.actions.append(action)

        # Sezione 2/10: prima si impostava host/porta/credenziali da
        # Console Python di QGIS -- ora c'e' una schermata dedicata,
        # raggiungibile sia dal menu sia (automaticamente) al primo
        # accesso se la connessione non e' ancora configurata.
        settings_action = QAction(
            QIcon(icon_path), "Impostazioni connessione...", self.iface.mainWindow()
        )
        settings_action.triggered.connect(self._show_connection_settings)
        self.iface.addPluginToMenu("&Matrice", settings_action)
        self.actions.append(settings_action)

        self.dock_widget = MainDockWidget(self.iface.mainWindow())
        self.dock_widget.setObjectName("MatriceDockWidget")
        self.dock_widget.hide()
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)

        # Collega i segnali dell'interfaccia alla logica applicativa.
        # Il login vero e proprio passa da LoginDialog (toggle_dock ->
        # _show_login_dialog -> _verify_login -> _dopo_login_riuscito): il
        # dock widget si occupa solo di logout e delle azioni successive
        # all'accesso.
        self.dock_widget.logout_requested.connect(self.handle_logout)
        self.dock_widget.cantiere_selected.connect(self.handle_open_tavola)
        self.dock_widget.quick_view_requested.connect(self.handle_quick_view)
        self.dock_widget.gps_import_requested.connect(self.handle_gps_import)
        self.dock_widget.layer_visibility_toggled.connect(self.handle_toggle_layer_visibility)
        self.dock_widget.populate_quick_views(self.quick_view_manager.available_views())

    def unload(self):
        """Chiamato da QGIS alla disattivazione: rimuove toolbar e dock."""
        for action in self.actions:
            self.iface.removePluginMenu("&Matrice", action)
            self.toolbar.removeAction(action)
        if self.toolbar is not None:
            del self.toolbar
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
        self.sync_handler.disconnect_all()
        self.point_sync_handler.disconnect_all()

    # ------------------------------------------------------------------
    # Handler collegati ai segnali del dock widget (sezione 2 e 4)
    # ------------------------------------------------------------------
    def toggle_dock(self):
        if self.dock_widget.isVisible():
            self.dock_widget.hide()
            return

        if not self.session.is_authenticated():
            if not MatriceConnection.is_configured():
                # Sezione 2: niente login se manca ancora la connessione al
                # database -- fallirebbe con un errore tecnico. Si apre
                # prima la schermata di configurazione.
                if not self._show_connection_settings():
                    return
            self._show_login_dialog()
        else:
            self.dock_widget.show()

    def _show_login_dialog(self):
        dialog = LoginDialog(
            self.iface.mainWindow(),
            remember_default=self.session.has_saved_credentials(),
            verify_callback=self._verify_login,
        )
        if dialog.exec_():
            # A questo punto il login e' gia' riuscito (_verify_login lo ha
            # gia' verificato prima di lasciare chiudere il dialogo): qui
            # resta solo da popolare l'elenco cantieri e mostrare il pannello.
            self._dopo_login_riuscito()

    def _verify_login(self, username, password, remember_me):
        """
        Chiamato da LoginDialog PRIMA di chiudersi (sezione 2): se le
        credenziali non sono valide, il dialogo resta aperto e mostra
        l'errore invece di chiudersi lasciando l'operatore senza alcun
        riscontro (bug corretto: l'errore finiva prima su un pannello
        ancora nascosto, dietro al dialogo di login).
        """
        return self.session.login(username, password, remember=remember_me)

    def _dopo_login_riuscito(self):
        """Sezione 2: popola l'elenco cantieri e mostra il pannello, dopo un login gia' verificato."""
        try:
            cantieri = self.matrice_api.list_cantieri_abilitati(self.session.access_token)
        except Exception as exc:
            self.dock_widget.show_error(str(exc))
            return
        self.dock_widget.populate_cantieri(cantieri)
        self.dock_widget.show()
        # Sfondi satellite/CTR (sezione ...): aggiunti una sola volta a
        # sessione QGIS, non duplicati se gia' presenti (BasemapManager).
        self.basemap_manager.ensure_default_basemaps()

    def _show_connection_settings(self):
        """
        Apre la schermata di configurazione connessione (sezione 2/10).
        Ritorna True se, al termine, la connessione risulta configurata
        (salvata ora o gia' presente da prima) -- usato da toggle_dock per
        decidere se proseguire subito con il login.
        """
        dialog = ConnectionSettingsDialog(self.iface.mainWindow())
        if dialog.exec_():
            ok, error_message = dialog.save_to_settings()
            if not ok:
                self.iface.messageBar().pushWarning("Matrice", error_message)
                return False
            # Se l'operatore ha cambiato URL/chiave Supabase (solo per un
            # ambiente di sviluppo/test), le letture successive devono
            # usarli subito, non quelli letti alla prima chiamata.
            self.session.reset_rest_client()
        return MatriceConnection.is_configured()

    def handle_logout(self):
        self.session.logout()
        self.dock_widget.reset()

    def handle_open_tavola(self, cantiere_id):
        """
        Sezione 4, passo 1: apertura tavola -> layer PostGIS nativi filtrati
        sul cantiere.

        Vengono aggiunti in mappa subito solo i livelli che hanno gia' dei
        dati per questo cantiere (LayerManager.load_cantiere decide in base
        al conteggio righe); gli altri restano disponibili a richiesta nel
        pannello "Livelli del cantiere", spuntabili per aggiungerli vuoti e
        iniziare a digitalizzare (vedi handle_toggle_layer_visibility).

        I layer si dividono su due percorsi di sincronizzazione distinti
        (sezione 5): rilievo_geometria/rilievo_quota (geometria PostGIS vera
        e propria, via SyncHandler) e i tre layer puntuali con lat/lng
        diretti -- carotaggio, colonna_stratigrafica, sezione_prospetto --
        che passano da PointPositionSyncHandler per la regola "quota
        manuale vince, altrimenti ricalcolo dal DEM".
        """
        self.session.cantiere_corrente_id = cantiere_id
        layers, has_data = self.layer_manager.load_cantiere(cantiere_id)

        for key, layer in layers.items():
            if has_data[key]:
                self._watch_layer_sync(key, layer, cantiere_id)

        layer_states = [(key, opts["display_name"], has_data[key]) for key, opts in LAYER_SOURCES.items()]
        self.dock_widget.populate_layer_toggles(cantiere_id, layer_states)

        fasi_rows = self.matrice_api.list_fasi(cantiere_id, self.session.access_token)
        self._fasi_correnti = [row["fase"] for row in fasi_rows]
        self.dock_widget.populate_fasi(fasi_rows)
        us_rows = self.matrice_api.list_us(cantiere_id, self.session.access_token, solo_attive=False)
        self.dock_widget.populate_us_attive(us_rows)

        # Stile di base del rilievo (sezione 6), solo se gia' in mappa: se
        # "Rilievo vettoriale" e' vuoto e non ancora mostrato, lo stile si
        # applica al momento in cui l'operatore lo aggiunge dalla spunta
        # (vedi handle_toggle_layer_visibility).
        if has_data.get("rilievo_geometria"):
            self._apply_us_style(layers["rilievo_geometria"])

    def _watch_layer_sync(self, key, layer, cantiere_id):
        """Aggancia il layer appena mostrato in mappa al percorso di sincronizzazione giusto (sezione 5)."""
        if key in LayerManager.POINT_SYNC_TABLES:
            table_name = LayerManager.POINT_SYNC_TABLES[key]
            self.point_sync_handler.watch(layer, table_name, cantiere_id)
        else:
            # rilievo_geometria si scrive sulla tabella base, non sulla
            # vista v_rilievo_geometria_qgis (sola lettura per i campi
            # extra); rilievo_quota coincide gia' con la propria tabella.
            table_name = "rilievo_geometria" if key == "rilievo_geometria" else key
            self.sync_handler.watch_layer(layer, table_name)

    def _apply_us_style(self, rilievo_layer):
        us_qml_path = os.path.join(PLUGIN_DIR, "config", "styles", "US.qml")
        try:
            self.style_manager.load_qml(
                rilievo_layer, us_qml_path, known_fields=[f.name() for f in rilievo_layer.fields()]
            )
        except Exception as exc:  # StyleValidationError o RuntimeError di QGIS
            self._show_warning(str(exc))

    def handle_toggle_layer_visibility(self, cantiere_id, key, checked):
        """
        Spunta nel pannello "Livelli del cantiere": aggiunge o rimuove un
        livello dalla mappa su richiesta esplicita dell'operatore (per i
        livelli senza dati, non caricati automaticamente da
        handle_open_tavola).
        """
        if checked:
            layer, era_gia_visibile = self.layer_manager.show_layer(cantiere_id, key)
            if not era_gia_visibile:
                self._watch_layer_sync(key, layer, cantiere_id)
                if key == "rilievo_geometria":
                    self._apply_us_style(layer)
        else:
            self.layer_manager.hide_layer(cantiere_id, key)

    def handle_quick_view(self, cantiere_id, view_key):
        """Sezione 6: applica in un solo clic filtro + stile di una vista rapida."""
        layers = self.layer_manager.current_layers(cantiere_id)
        self.quick_view_manager.apply(view_key, layers, fasi=self._fasi_correnti)

    def handle_gps_import(self, cantiere_id):
        """
        Sezione 4, passo 3: apre il dialogo "Importa rilievo GPS". Tutta la
        logica (scelta file, anteprima abbinamenti, conferma prima della
        scrittura) e' nel dialogo stesso; qui si passa solo cosa serve per
        parlare con Matrice (gps_importer) e il cantiere corrente.
        """
        dialog = GpsImportDialog(self.iface.mainWindow(), self.gps_importer, cantiere_id)
        dialog.exec_()

    def _show_warning(self, message):
        """Avviso non bloccante in linguaggio semplice (es. quota non ricalcolata: DEM mancante)."""
        if self.dock_widget is not None:
            self.dock_widget.show_warning(message)
