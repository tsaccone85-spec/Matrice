"""
Classe principale del plugin: registra il pannello (dock widget) in QGIS,
gestisce il ciclo di vita (initGui/unload) e fa da ponte tra l'interfaccia
utente e i moduli di core/.

Riferimento: sezione 10 della specifica tecnica ("Struttura tecnica del
plugin PyQGIS").
"""

import os

import configparser

from qgis.core import QgsApplication, QgsFeatureRequest, QgsGeometry, QgsProject, QgsRectangle
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QFileDialog, QMessageBox

from .core.auth import SessionManager
from .core.basemap_manager import BasemapManager
from .core.db_connector import MatriceConnection
from .core.dem_sampler import DemSampler
from .core.gps_import import GpsImporter
from .core.join_elenchi import aggiungi_join_elenchi
from .core.layer_manager import LAYER_SOURCES, LayerManager
from .core.localizzazione_manager import LocalizzazioneManager
from .core.matrice_api import MatriceApi
from .core.point_position_sync import PointPositionSyncHandler
from .core.print_layout_manager import PrintLayoutManager
from .core.sync_handler import SyncHandler
from .core.style_manager import StyleManager
from .core.quick_views import QuickViewManager
from .ui.main_dock_widget import MainDockWidget
from .ui.login_dialog import LoginDialog
from .ui.connection_settings_dialog import ConnectionSettingsDialog
from .ui.gps_import_dialog import GpsImportDialog
from .ui.punto_dettaglio_dialog import PuntoDettaglioDialog
from .ui.us_detail_dialog import UsDetailDialog

PLUGIN_DIR = os.path.dirname(__file__)


class _CanvasClickFilter(QObject):
    """
    installEventFilter() richiede un vero QObject: MatricePlugin non lo e'
    (e non conviene farlo diventare tale solo per questo), quindi i click
    sulla canvas passano da questo piccolo oggetto dedicato, che si limita
    a inoltrare l'evento (doppio click, o click singolo a bottone
    rilasciato) a una callback qualunque.
    """

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease):
            if self._callback(event):
                return True
        return False


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
        self.localizzazione_manager = LocalizzazioneManager()
        self.print_layout_manager = PrintLayoutManager(
            os.path.join(PLUGIN_DIR, "config", "layouts", "localizzazione_1_200.qpt")
        )
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
        """
        Chiamato da QGIS all'avvio: crea la barra Matrice e il dock widget.

        La barra (Configura / Accedi / Info) e' pensata per l'operatore non
        ancora autenticato: una volta fatto l'accesso non serve piu' (il
        pannello con cantieri/livelli la sostituisce del tutto), quindi
        sparisce -- ricompare al logout (handle_logout). Le stesse azioni
        restano comunque raggiungibili dal menu "Matrice" in ogni momento,
        anche a barra nascosta.
        """
        self.toolbar = self.iface.addToolBar("Matrice")
        self.toolbar.setObjectName("MatriceToolbar")

        # Icone standard del tema QGIS (QgsApplication.getThemeIcon), una
        # per azione invece di matrice.png ripetuto 4 volte: niente asset
        # nuovi da mantenere, e restano coerenti col resto dell'interfaccia
        # (e con un eventuale tema scuro, che le icone del tema seguono).
        icona_configura = QgsApplication.getThemeIcon("/mActionOptions.svg")
        icona_accedi = QgsApplication.getThemeIcon("/mIconFieldKey.svg")
        icona_join = QgsApplication.getThemeIcon("/mActionJoinSimplify.svg")
        icona_info = QgsApplication.getThemeIcon("/mActionPropertiesWidget.svg")

        # Sezione 2/10: prima si impostava host/porta/credenziali da
        # Console Python di QGIS -- ora c'e' una schermata dedicata,
        # raggiungibile sia dal menu/barra sia (automaticamente) al primo
        # accesso se la connessione non e' ancora configurata.
        configura_action = QAction(icona_configura, "Configura", self.iface.mainWindow())
        configura_action.triggered.connect(self._show_connection_settings)
        self.toolbar.addAction(configura_action)
        self.iface.addPluginToMenu("&Matrice", configura_action)
        self.actions.append(configura_action)

        accedi_action = QAction(icona_accedi, "Accedi", self.iface.mainWindow())
        accedi_action.triggered.connect(self._on_accedi_clicked)
        self.toolbar.addAction(accedi_action)
        self.iface.addPluginToMenu("&Matrice", accedi_action)
        self.actions.append(accedi_action)

        join_action = QAction(icona_join, "Unisci dati elenchi", self.iface.mainWindow())
        join_action.triggered.connect(self._join_elenchi_attivo)
        self.toolbar.addAction(join_action)
        self.iface.addPluginToMenu("&Matrice", join_action)
        self.actions.append(join_action)

        info_action = QAction(icona_info, "Info", self.iface.mainWindow())
        info_action.triggered.connect(self._show_info)
        self.toolbar.addAction(info_action)
        self.iface.addPluginToMenu("&Matrice", info_action)
        self.actions.append(info_action)

        self.dock_widget = MainDockWidget(self.iface.mainWindow())
        self.dock_widget.setObjectName("MatriceDockWidget")
        self.dock_widget.hide()
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)

        # Collega i segnali dell'interfaccia alla logica applicativa.
        # Il login vero e proprio passa da LoginDialog (_on_accedi_clicked ->
        # _show_login_dialog -> _verify_login -> _dopo_login_riuscito): il
        # dock widget si occupa solo di logout e delle azioni successive
        # all'accesso.
        self.dock_widget.logout_requested.connect(self.handle_logout)
        self.dock_widget.cantiere_selected.connect(self.handle_open_tavola)
        self.dock_widget.quick_view_requested.connect(self.handle_quick_view)
        self.dock_widget.gps_import_requested.connect(self.handle_gps_import)
        self.dock_widget.layer_visibility_toggled.connect(self.handle_toggle_layer_visibility)
        self.dock_widget.salva_area_requested.connect(self.handle_salva_area)
        self.dock_widget.stampa_localizzazione_requested.connect(self.handle_stampa_localizzazione)

        # Scheda US completa (doppio click su un elemento gia' selezionato
        # del layer "Rilievo vettoriale"): niente strumento mappa dedicato,
        # si intercetta il doppio click sulla canvas con un event filter.
        self._canvas_click_filter = _CanvasClickFilter(self._handle_canvas_click)
        self.iface.mapCanvas().viewport().installEventFilter(self._canvas_click_filter)
        self.dock_widget.populate_quick_views(self.quick_view_manager.available_views())

        # Pannello "US attive" (sezione 8): ha senso solo mentre si lavora
        # sul layer "Rilievo vettoriale" -- nascosto quando e' attivo un
        # altro layer (vedi _handle_active_layer_changed).
        self.iface.currentLayerChanged.connect(self._handle_active_layer_changed)

    def unload(self):
        """Chiamato da QGIS alla disattivazione: rimuove toolbar e dock."""
        self.iface.currentLayerChanged.disconnect(self._handle_active_layer_changed)
        self.iface.mapCanvas().viewport().removeEventFilter(self._canvas_click_filter)
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
    def _on_accedi_clicked(self):
        if self.session.is_authenticated():
            self.dock_widget.show()  # sessione gia' attiva (barra non ancora nascosta per qualche motivo)
            return
        if not MatriceConnection.is_configured():
            # Sezione 2: niente login se manca ancora la connessione al
            # database -- fallirebbe con un errore tecnico. Si apre
            # prima la schermata di configurazione.
            if not self._show_connection_settings():
                return
        self._show_login_dialog()

    def _join_elenchi_attivo(self):
        """
        Aggancia le schede US (elenchi di Matrice) al rilievo del layer attivo,
        sul numero US. Il layer attivo deve essere il rilievo di un cantiere aperto.
        """
        layer = self.iface.activeLayer()
        cantiere_id = self.layer_manager.cantiere_del_layer(layer)
        if cantiere_id is None:
            self._show_warning("Seleziona il layer del rilievo di un cantiere aperto da Matrice.")
            return
        token = self.session.access_token
        try:
            righe = self.matrice_api.list_us(cantiere_id, token, solo_attive=False)
        except Exception as e:
            self._show_warning(f"Non riesco a leggere le schede US da Matrice: {e}")
            return
        esito = aggiungi_join_elenchi(layer, righe)
        messaggio = (
            f"Schede agganciate ai poligoni: {esito['agganciati']}.\n"
            f"Poligoni senza scheda: {len(esito['senza_scheda'])}"
            + (f" (US {', '.join(esito['senza_scheda'])})" if esito["senza_scheda"] else "")
            + f".\nSchede senza poligono: {len(esito['senza_poligono'])}"
            + (f" (US {', '.join(esito['senza_poligono'])})" if esito["senza_poligono"] else "")
            + "."
        )
        QMessageBox.information(self.iface.mainWindow(), "Unione dati elenchi", messaggio)

    def _show_info(self):
        """Informazioni sul plugin (nome, versione, descrizione), lette da metadata.txt."""
        parser = configparser.ConfigParser()
        parser.read(os.path.join(PLUGIN_DIR, "metadata.txt"), encoding="utf-8")
        nome = parser.get("general", "name", fallback="Matrice")
        versione = parser.get("general", "version", fallback="")
        descrizione = parser.get("general", "about", fallback="")
        QMessageBox.information(self.iface.mainWindow(), "Informazioni su Matrice", f"{nome} - v{versione}\n\n{descrizione}")

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
        # La barra Configura/Accedi/Info non serve piu' una volta dentro:
        # il pannello appena mostrato la sostituisce (ricompare al logout,
        # vedi handle_logout). Le stesse azioni restano nel menu "Matrice".
        self.toolbar.setVisible(False)
        # Sfondi satellite/CTR (sezione ...): aggiunti una sola volta a
        # sessione QGIS, non duplicati se gia' presenti (BasemapManager).
        # Google -> Esri in automatico se Google non e' raggiungibile; un
        # avviso esplicito solo se falliscono entrambi, invece di lasciare
        # l'operatore senza sfondo satellitare e senza nessun riscontro.
        if not self.basemap_manager.ensure_default_basemaps():
            self._show_warning(
                "Impossibile caricare lo sfondo satellitare (Google e Esri entrambi non raggiungibili): "
                "verificare la connessione Internet."
            )

    def _show_connection_settings(self):
        """
        Apre la schermata di configurazione connessione (sezione 2/10).
        Ritorna True se, al termine, la connessione risulta configurata
        (salvata ora o gia' presente da prima) -- usato da _on_accedi_clicked
        per decidere se proseguire subito con il login.
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
        self.dock_widget.hide()
        self.toolbar.setVisible(True)

    def handle_open_tavola(self, cantiere_id, nome_cantiere=None):
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
        cantiere_precedente = self.session.cantiere_corrente_id
        if cantiere_precedente and cantiere_precedente != cantiere_id:
            self._rimuovi_layer_cantiere(cantiere_precedente)

        self.session.cantiere_corrente_id = cantiere_id
        layers, has_data = self.layer_manager.load_cantiere(cantiere_id, nome_cantiere)

        # Sfondo CTR della Regione del cantiere (core/basemap_manager.py),
        # se nota e raggiungibile -- mai bloccante: se la lettura della
        # regione o il servizio WMS falliscono, resta il satellite.
        try:
            regione = self.matrice_api.get_regione_cantiere(cantiere_id, self.session.access_token)
            self.basemap_manager.ensure_ctr_per_regione(regione)
        except Exception:
            pass

        # Localizzazione dell'intervento (punto gia' registrato su Matrice) e
        # area disegnata: due layer del tutto separati (vedi
        # core/localizzazione_manager.py), ciascuno aggiunto subito in mappa
        # solo se ha gia' un dato su Matrice -- stessa regola "solo i livelli
        # con dati, gli altri a richiesta" degli altri livelli del cantiere
        # (LayerManager.load_cantiere). Mai bloccante: Matrice non obbliga
        # nessun cantiere ad avere ne' un punto ne' un'area registrati.
        try:
            localizzazione = self.matrice_api.get_localizzazione_cantiere(cantiere_id, self.session.access_token)
        except Exception:
            localizzazione = None

        ha_punto = bool(localizzazione and localizzazione.get("lat") is not None and localizzazione.get("lng") is not None)
        ha_area = bool(localizzazione and localizzazione.get("area_geom_wkt"))
        if ha_punto:
            self.localizzazione_manager.layer_punto(
                cantiere_id, nome_cantiere, localizzazione["lat"], localizzazione["lng"]
            )
        if ha_area:
            self._apply_area_style(
                self.localizzazione_manager.layer_area(cantiere_id, nome_cantiere, localizzazione["area_geom_wkt"])
            )

        for key, layer in layers.items():
            if has_data[key]:
                self._watch_layer_sync(key, layer, cantiere_id)

        layer_states = [(key, opts["display_name"], has_data[key]) for key, opts in LAYER_SOURCES.items()]
        # "Localizzazione di cantiere" e "Area di cantiere" non sono layer
        # PostGIS come gli altri (vedi core/localizzazione_manager.py): due
        # voci distinte, ciascuna spuntata solo se il relativo dato era gia'
        # presente su Matrice all'apertura (coerente con ha_punto/ha_area
        # appena calcolati, che hanno gia' deciso se crearli in mappa).
        layer_states.append(("localizzazione_cantiere", "Localizzazione di cantiere", ha_punto))
        layer_states.append(("area_intervento", "Area di cantiere", ha_area))
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

        # Colore fisso per esito (rosso/verde) di colonne stratigrafiche e
        # carotaggi, sempre applicato appena il layer compare in mappa -- non
        # solo scegliendo una vista rapida (sezione 6).
        if has_data.get("colonna_stratigrafica"):
            self.style_manager.apply_esito_style(layers["colonna_stratigrafica"])
        if has_data.get("carotaggio"):
            self.style_manager.apply_esito_style(layers["carotaggio"])

        # Stato iniziale del pannello "US attive" per questo cantiere (il
        # layer attivo in QGIS potrebbe non essere cambiato dall'apertura
        # della tavola precedente, quindi currentLayerChanged non scatta da
        # solo -- va valutato subito anche qui).
        self._handle_active_layer_changed(self.iface.activeLayer())

    def _handle_active_layer_changed(self, layer):
        """
        "US attive" ha senso solo mentre si lavora sul layer "Rilievo
        vettoriale" (sezione 8): mostrato/nascosto in base al layer
        attivato dall'operatore nel pannello Livelli di QGIS.
        """
        cantiere_id = self.session.cantiere_corrente_id
        rilievo = self.layer_manager.current_layers(cantiere_id).get("rilievo_geometria") if cantiere_id else None
        self.dock_widget.set_us_attive_visibile(layer is not None and rilievo is not None and layer.id() == rilievo.id())

        # Viste rapide legate a un layer specifico (oggi: le 3 viste
        # sull'esito delle colonne stratigrafiche) compaiono nel menu solo
        # mentre quel layer e' quello attivo in QGIS -- stesso principio di
        # "US attive" sopra, applicato al menu delle viste rapide.
        chiave_attiva = self._chiave_layer_attivo(cantiere_id, layer)
        self.dock_widget.populate_quick_views(self.quick_view_manager.available_views(chiave_attiva))

    def _chiave_layer_attivo(self, cantiere_id, layer):
        """Chiave in LAYER_SOURCES del layer QGIS attivo, o None se non e' uno dei layer di Matrice."""
        if cantiere_id is None or layer is None:
            return None
        for key, candidato in self.layer_manager.current_layers(cantiere_id).items():
            if candidato.id() == layer.id():
                return key
        return None

    def _rimuovi_layer_cantiere(self, cantiere_id):
        """
        Pulisce la mappa dai layer del cantiere lasciato quando si apre la
        tavola di un cantiere diverso: mai due cantieri insieme in mappa,
        per non confondere l'operatore su quale sia quello attivo. Riguarda
        sia i layer PostGIS (LayerManager) sia i due layer di
        localizzazione/area (LocalizzazioneManager) -- quest'ultimo avvisa
        se c'era un'area disegnata ma non ancora salvata su Matrice, che va
        persa con la rimozione.

        disconnect_all() qui è sicuro: in questo plugin e' sempre aperta la
        tavola di un solo cantiere per volta, quindi i layer attualmente
        agganciati alla sincronizzazione sono sempre e solo quelli del
        cantiere che si sta per rimuovere.
        """
        self.sync_handler.disconnect_all()
        self.point_sync_handler.disconnect_all()
        self.layer_manager.remove_cantiere(cantiere_id)
        area_non_salvata = self.localizzazione_manager.remove_cantiere(cantiere_id)
        if area_non_salvata:
            self._show_warning(
                "L'area di cantiere disegnata per il cantiere precedente non era stata salvata su Matrice: "
                "e' stata scartata passando al nuovo cantiere."
            )

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

    def _apply_area_style(self, area_layer):
        """Stile fornito dall'operatore per "Area di cantiere (disegno)" (sezione 7), uguale per tutti."""
        area_qml_path = os.path.join(PLUGIN_DIR, "config", "styles", "Area_di_cantiere.qml")
        try:
            self.style_manager.load_qml(
                area_layer, area_qml_path, known_fields=[f.name() for f in area_layer.fields()]
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
        if key == "area_intervento":
            self._handle_toggle_area_intervento(cantiere_id, checked)
            return
        if key == "localizzazione_cantiere":
            self._handle_toggle_localizzazione(cantiere_id, checked)
            return

        if checked:
            layer, era_gia_visibile = self.layer_manager.show_layer(cantiere_id, key)
            if not era_gia_visibile:
                self._watch_layer_sync(key, layer, cantiere_id)
                if key == "rilievo_geometria":
                    self._apply_us_style(layer)
                elif key in ("colonna_stratigrafica", "carotaggio"):
                    self.style_manager.apply_esito_style(layer)
        else:
            self.layer_manager.hide_layer(cantiere_id, key)

    def _ottieni_localizzazione(self, cantiere_id):
        """
        Lettura di cantiere.lat/lng/area_geom_wkt condivisa dai due toggle
        qui sotto (punto e area): un eventuale errore (rete, permessi) va
        mostrato all'operatore solo quando lo richiede esplicitamente
        spuntando la voce, non quando viene gia' mostrata l'eventuale
        presente, come fa handle_open_tavola.
        """
        try:
            return self.matrice_api.get_localizzazione_cantiere(cantiere_id, self.session.access_token)
        except Exception as exc:
            self._show_warning(str(exc))
            return None

    def _handle_toggle_area_intervento(self, cantiere_id, checked):
        """
        "Area di cantiere" non e' un layer PostGIS: non va mai distrutto per
        nasconderlo (si perderebbe un'eventuale area disegnata ma non
        ancora salvata, vedi core/localizzazione_manager.py) -- la spunta
        agisce solo sulla visibilita' nel pannello Livelli.
        """
        if not self.localizzazione_manager.layer_area_esiste(cantiere_id):
            if not checked:
                return  # niente da nascondere
            localizzazione = self._ottieni_localizzazione(cantiere_id)
            if localizzazione is None:
                return
            nome_cantiere = self.layer_manager.nome_cantiere(cantiere_id) or cantiere_id
            self._apply_area_style(
                self.localizzazione_manager.layer_area(cantiere_id, nome_cantiere, localizzazione.get("area_geom_wkt"))
            )
        else:
            self.localizzazione_manager.set_area_visibile(cantiere_id, checked)

    def _handle_toggle_localizzazione(self, cantiere_id, checked):
        """
        "Localizzazione di cantiere" (il punto, separato dall'area -- vedi
        core/localizzazione_manager.py): stessa logica di
        _handle_toggle_area_intervento, su un layer indipendente.
        """
        if not self.localizzazione_manager.layer_punto_esiste(cantiere_id):
            if not checked:
                return
            localizzazione = self._ottieni_localizzazione(cantiere_id)
            if localizzazione is None:
                return
            nome_cantiere = self.layer_manager.nome_cantiere(cantiere_id) or cantiere_id
            self.localizzazione_manager.layer_punto(
                cantiere_id, nome_cantiere, localizzazione.get("lat"), localizzazione.get("lng")
            )
        else:
            self.localizzazione_manager.set_punto_visibile(cantiere_id, checked)

    def _handle_canvas_click(self, event):
        """
        Callback di _CanvasClickFilter: apre la scheda di dettaglio
        dell'elemento sotto il punto cliccato, su uno dei layer collegati a
        una scheda (Rilievo vettoriale -> scheda US, Colonne
        stratigrafiche/Carotaggi -> propria scheda) del cantiere corrente.

        Due modi per attivarla:
        - doppio click diretto, sempre, qualunque sia lo strumento attivo;
        - click singolo, ma solo quando lo strumento attivo e' "Identifica
          elementi" di QGIS -- cosi' la nostra scheda (editabile, con
          anteprime foto) sostituisce il pannello nativo "Informazioni
          Risultati" per questi layer, senza toccare in nessun modo
          editing/selezione/altri strumenti.

        Ritorna True se ha trovato ed aperto qualcosa (per farlo consumare
        dall'event filter, cosi' il pannello nativo non si apre a sua
        volta per lo stesso click), False altrimenti -- in quel caso gli
        strumenti normali di QGIS (identifica su altri layer, zoom con
        doppio click, ecc.) restano invariati. Se il punto cade su piu'
        elementi/layer contemporaneamente, ha la precedenza il layer
        attivo in quel momento nel pannello Livelli.
        """
        e_doppio_click = event.type() == QEvent.MouseButtonDblClick
        if not e_doppio_click:
            if event.button() != Qt.LeftButton:
                return False
            strumento_attivo = self.iface.mapCanvas().mapTool()
            # QgsMapToolIdentifyAction (lo strumento "Identifica elementi"
            # usato dall'applicazione QGIS) e' una classe interna
            # dell'app, non esposta nei binding Python pubblici di
            # qgis.gui in tutte le versioni di QGIS (ImportError altrimenti
            # bloccante per l'intero plugin): si confronta il nome della
            # classe come stringa invece di importarla per un isinstance().
            if strumento_attivo is None or type(strumento_attivo).__name__ != "QgsMapToolIdentifyAction":
                return False  # click singolo con un altro strumento: non e' affar nostro

        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            return False
        layers = self.layer_manager.current_layers(cantiere_id)
        layer_attivo = self.iface.activeLayer()

        canvas = self.iface.mapCanvas()
        punto_mappa = canvas.getCoordinateTransform().toMapCoordinates(event.pos())
        raggio = QgsMapTool.searchRadiusMU(canvas)
        rettangolo = QgsRectangle(
            punto_mappa.x() - raggio, punto_mappa.y() - raggio,
            punto_mappa.x() + raggio, punto_mappa.y() + raggio,
        )

        candidati = (
            ("rilievo_geometria", self._apri_scheda_us, "us_id"),
            ("colonna_stratigrafica", self._apri_scheda_colonna, "id"),
            ("carotaggio", self._apri_scheda_carotaggio, "id"),
        )
        candidati = sorted(candidati, key=lambda c: layers.get(c[0]) is not layer_attivo)

        for chiave, apri, campo_id in candidati:
            layer = layers.get(chiave)
            if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
                continue  # non costruito, o non attualmente in mappa (vedi LayerManager.hide_layer)
            feature = next(layer.getFeatures(QgsFeatureRequest().setFilterRect(rettangolo)), None)
            if feature is not None:
                apri(feature[campo_id])
                return True
        return False

    def _apri_scheda_us(self, us_id):
        if not us_id:
            self._show_warning("Questo elemento non e' collegato a nessuna US.")
            return
        dialog = UsDetailDialog(self.matrice_api, self.session, us_id, self.iface.mainWindow())
        dialog.exec_()

    def _apri_scheda_colonna(self, colonna_id):
        dialog = PuntoDettaglioDialog(
            self.matrice_api,
            self.session,
            "Colonna stratigrafica",
            self.matrice_api.get_colonna_stratigrafica,
            self.matrice_api.update_colonna_stratigrafica,
            colonna_id,
            self.iface.mainWindow(),
        )
        dialog.exec_()

    def _apri_scheda_carotaggio(self, carotaggio_id):
        dialog = PuntoDettaglioDialog(
            self.matrice_api,
            self.session,
            "Carotaggio",
            self.matrice_api.get_carotaggio,
            self.matrice_api.update_carotaggio,
            carotaggio_id,
            self.iface.mainWindow(),
        )
        dialog.exec_()

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

    def handle_salva_area(self, cantiere_id):
        """
        Scrive su Matrice (cantiere.area_geom_wkt) il poligono disegnato nel
        layer "Area intervento (disegno)". Richiede il ruolo "referente"
        (RLS cantiere_update): un operatore senza quel ruolo vede un
        messaggio di permessi chiaro, non un errore tecnico grezzo.
        """
        wkt = self.localizzazione_manager.wkt_corrente(cantiere_id)
        if wkt is None:
            self._show_warning(
                'Nessuna area disegnata: attivare l\'editing sul layer "Area di cantiere (disegno)" '
                "e disegnare un poligono prima di salvare."
            )
            return
        try:
            self.matrice_api.update_area_intervento(cantiere_id, wkt, self.session.access_token)
        except Exception as exc:
            self._show_warning(str(exc))
            return
        self.iface.messageBar().pushSuccess("Matrice", "Area dell'intervento salvata.")

    def handle_stampa_localizzazione(self, cantiere_id):
        """
        Esporta in PDF la tavola di localizzazione del cantiere a scala
        1:200 (sezione 7): layout riusato se gia' creato in precedenza (vedi
        PrintLayoutManager, eventuali personalizzazioni dell'operatore come
        un logo restano), mappa centrata sul punto registrato su Matrice o,
        in mancanza, sul centro dell'area disegnata.
        """
        localizzazione = self._ottieni_localizzazione(cantiere_id)
        if localizzazione is None:
            return

        lat, lng = localizzazione.get("lat"), localizzazione.get("lng")
        if lat is None or lng is None:
            area_wkt = localizzazione.get("area_geom_wkt")
            centro = QgsGeometry.fromWkt(area_wkt).centroid().asPoint() if area_wkt else None
            if centro is None:
                self._show_warning(
                    "Questo cantiere non ha ne' una posizione ne' un'area registrata su Matrice: niente da stampare."
                )
                return
            lat, lng = centro.y(), centro.x()

        nome_cantiere = self.layer_manager.nome_cantiere(cantiere_id) or cantiere_id
        percorso_pdf, _ = QFileDialog.getSaveFileName(
            self.dock_widget,
            "Esporta tavola di localizzazione",
            f"{nome_cantiere} - Localizzazione.pdf",
            "PDF (*.pdf)",
        )
        if not percorso_pdf:
            return

        layer_punto = self.localizzazione_manager.layer_punto(cantiere_id, nome_cantiere, lat, lng)
        layer_area = self.localizzazione_manager.layer_area(cantiere_id, nome_cantiere, localizzazione.get("area_geom_wkt"))
        self._apply_area_style(layer_area)
        sfondo = self.basemap_manager.layer_sfondo_corrente()
        layers = [l for l in (layer_punto, layer_area, sfondo) if l is not None]

        layout = self.print_layout_manager.get_or_create_layout(nome_cantiere)
        self.print_layout_manager.aggiorna_titolo(layout, nome_cantiere)
        self.print_layout_manager.aggiorna_mappa(layout, layers, lat, lng)
        try:
            self.print_layout_manager.esporta_pdf(layout, percorso_pdf)
        except Exception as exc:
            self._show_warning(str(exc))
            return
        self.iface.messageBar().pushSuccess("Matrice", f"Tavola di localizzazione esportata in {percorso_pdf}.")

    def _show_warning(self, message):
        """Avviso non bloccante in linguaggio semplice (es. quota non ricalcolata: DEM mancante)."""
        if self.dock_widget is not None:
            self.dock_widget.show_warning(message)
