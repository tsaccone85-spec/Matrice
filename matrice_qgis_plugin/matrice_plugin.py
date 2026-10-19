"""
Classe principale del plugin: registra il pannello (dock widget) in QGIS,
gestisce il ciclo di vita (initGui/unload) e fa da ponte tra l'interfaccia
utente e i moduli di core/.

Riferimento: sezione 10 della specifica tecnica ("Struttura tecnica del
plugin PyQGIS").
"""

import os

import configparser

from qgis.core import QgsApplication, QgsDefaultValue, QgsCoordinateReferenceSystem, QgsFeature, QgsFeatureRequest, QgsGeometry, QgsProject, QgsRectangle
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QFileDialog, QInputDialog, QMenu, QMessageBox, QToolButton

from .core.auth import SessionManager
from .core.basemap_manager import BasemapManager
from .core.db_connector import MatriceConnection
from .core.dem_sampler import DemSampler
from .core.gps_import import GpsImporter
from .core.join_elenchi import aggiungi_join_elenchi
from .core.layer_manager import LAYER_SOURCES, RIFERIMENTI_PER_TIPO_INTERVENTO, LayerManager

# Raggruppamento del pannello "Livelli del cantiere" per flusso di lavoro
# (chiesto da Tommaso, 2026-10-13): i livelli provenienti dalla fase di
# progettazione (cropmark, trincee, saggi) stanno sotto un unico gruppo
# "Progettazione" invece di restare sparsi fra gli altri livelli scavo.
# Chiave non presente = livello non raggruppato, resta in cima come prima.
_GRUPPO_LIVELLI = {
    "progettazione_cropmark": "Progettazione",
    "progettazione_vincolo": "Progettazione",
    "trincea_progettazione": "Progettazione",
    "saggio_progettazione": "Progettazione",
    "survey_ur": "Progettazione",
    "survey_ritrovamento": "Progettazione",
    "carotaggio_progettazione": "Progettazione",
}
from .core.localizzazione_manager import LocalizzazioneManager, calcola_buffer_wkt
from .core.matrice_api import MatriceApi
from .core.point_position_sync import PointPositionSyncHandler
from .core.print_layout_manager import PrintLayoutManager
from .core.rettangolo_progettazione import PosizionaRettangoloMapTool
from .core.ritrovamento_progettazione import PosizionaPuntoMapTool
from .core.sistema_riferimento import SISTEMI_RIFERIMENTO_EPSG
from .core.sync_handler import SyncHandler
from .core.vista_cantiere import estensione_cantiere_wgs84, vista_canvas_valida, zoom_su_estensione_wgs84
from .core.style_manager import StyleManager
from .core.quick_views import QuickViewManager
from .ui.main_dock_widget import MainDockWidget
from .ui.login_dialog import LoginDialog
from .ui.connection_settings_dialog import ConnectionSettingsDialog
from .ui.gps_import_dialog import GpsImportDialog
from .ui.nuovo_cantiere_dialog import NuovoCantiereDialog
from .ui.punto_dettaglio_dialog import PuntoDettaglioDialog
from .ui.buffer_area_dialog import BufferAreaDialog
from .ui.carotaggio_progettazione_dialog import CarotaggioProgettazioneDialog
from .ui.rettangolo_progettazione_dialog import RettangoloProgettazioneDialog
from .ui.us_detail_dialog import UsDetailDialog

# Chiavi LAYER_SOURCES (layer_manager.py) che attivano il menu "Progettazione"
# della toolbar: un rettangolo (trincea o saggio), una UR/rinvenimento o un
# carotaggio si creano solo mentre uno di questi layer e' quello attivo in
# QGIS (vedi _handle_active_layer_changed). "carotaggio" non fa parte del
# flusso "Progettazione Archeologica" in senso stretto (e' uno dei tre layer
# puntuali lat/lng sincronizzati da PointPositionSyncHandler), ma l'operatore
# vuole crearlo con lo stesso identico percorso -- attiva il livello, si
# accende il menu, "Carotaggio..." chiede le misure, click sulla mappa per
# posizionarlo -- quindi resta nello stesso elenco invece di avere un
# meccanismo di attivazione separato e meno intuitivo.
CHIAVI_LAYER_PROGETTAZIONE = (
    "trincea_progettazione", "saggio_progettazione", "survey_ur", "survey_ritrovamento", "carotaggio_progettazione",
)

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

        # Sezione 4: alternativa al disegno a mano del poligono "Area di
        # cantiere" -- importa la prima geometria di uno shapefile/
        # GeoPackage/KML (LocalizzazioneManager.wkt_da_file) e la salva
        # direttamente su Matrice, stesso percorso di scrittura di
        # handle_salva_area.
        icona_importa_area = QgsApplication.getThemeIcon("/mActionAddOgrLayer.svg")
        importa_area_action = QAction(icona_importa_area, "Importa area da file…", self.iface.mainWindow())
        importa_area_action.triggered.connect(self._handle_importa_area_da_file)
        self.toolbar.addAction(importa_area_action)
        self.iface.addPluginToMenu("&Matrice", importa_area_action)
        self.actions.append(importa_area_action)

        # Menu "Progettazione" (Trincea/Saggio/Ruota): abilitato solo mentre
        # e' attivo uno dei due layer Progettazione (vedi
        # _handle_active_layer_changed), stesso principio di "US attive" e
        # delle viste rapide qui sopra.
        icona_progettazione = QgsApplication.getThemeIcon("/mActionAddPolygon.svg")
        self.progettazione_action = QAction(icona_progettazione, "Progettazione", self.iface.mainWindow())
        menu_progettazione = QMenu(self.iface.mainWindow())
        azione_trincea = menu_progettazione.addAction("Trincea…")
        azione_trincea.triggered.connect(lambda: self._apri_nuovo_rettangolo("trincea"))
        azione_saggio = menu_progettazione.addAction("Saggio…")
        azione_saggio.triggered.connect(lambda: self._apri_nuovo_rettangolo("saggio"))
        azione_ur = menu_progettazione.addAction("Unità di Ricognizione (UR)…")
        azione_ur.triggered.connect(self._disegna_unita_ricognizione)
        azione_carotaggio = menu_progettazione.addAction("Carotaggio…")
        azione_carotaggio.triggered.connect(self._apri_nuovo_carotaggio)
        azione_buffer = menu_progettazione.addAction("Crea buffer area di indagine…")
        azione_buffer.triggered.connect(self._crea_buffer_area)
        menu_progettazione.addSeparator()
        azione_sposta = menu_progettazione.addAction("Sposta elemento")
        azione_sposta.triggered.connect(self._sposta_elemento_progettazione)
        azione_ruota = menu_progettazione.addAction("Ruota elemento")
        azione_ruota.triggered.connect(self._ruota_elemento_progettazione)
        menu_progettazione.addSeparator()
        self.azione_chiudi_progettazione = menu_progettazione.addAction("Chiudi progettazione…")
        self.azione_chiudi_progettazione.triggered.connect(self._chiudi_progettazione)
        self.azione_riapri_progettazione = menu_progettazione.addAction("Riapri progettazione")
        self.azione_riapri_progettazione.triggered.connect(self._riapri_progettazione)
        self.azione_riapri_progettazione.setVisible(False)
        # Passaggio ai flussi a valle: visibili a progettazione chiusa.
        self.azione_avvia_carotaggi = menu_progettazione.addAction("Avvia Lettura carotaggi…")
        self.azione_avvia_carotaggi.triggered.connect(lambda: self._avvia_flusso_da_progettazione("carotaggi"))
        self.azione_avvia_carotaggi.setVisible(False)
        self.azione_avvia_sorveglianza = menu_progettazione.addAction("Avvia Assistenza in corso d'opera…")
        self.azione_avvia_sorveglianza.triggered.connect(lambda: self._avvia_flusso_da_progettazione("sorveglianza"))
        self.azione_avvia_sorveglianza.setVisible(False)
        # Nei cantieri a valle (carotaggi/sorveglianza): scelta della Progettazione di riferimento.
        self.azione_collega_progettazione = menu_progettazione.addAction("Collega progettazione di riferimento…")
        self.azione_collega_progettazione.triggered.connect(self._collega_progettazione)
        self.azione_collega_progettazione.setVisible(False)
        # Voci che modificano la progettazione: spente quando e' chiusa (stand-by).
        self._azioni_modifica_progettazione = [
            azione_trincea, azione_saggio, azione_ur, azione_carotaggio,
            azione_buffer, azione_sposta, azione_ruota,
        ]
        self._stato_progettazione = None
        self._tipo_intervento_corrente = None
        self._progettazione_origine_id = None
        self.progettazione_action.setMenu(menu_progettazione)
        self.progettazione_action.setEnabled(False)
        self.toolbar.addAction(self.progettazione_action)
        self.toolbar.widgetForAction(self.progettazione_action).setPopupMode(QToolButton.InstantPopup)
        self.actions.append(self.progettazione_action)

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
        self.dock_widget.nuovo_cantiere_richiesto.connect(self._handle_nuovo_cantiere)
        self.dock_widget.aggiorna_cantieri_richiesto.connect(self._handle_aggiorna_cantieri)

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
        username_salvato, password_salvata = self.session.saved_credentials()
        dialog = LoginDialog(
            self.iface.mainWindow(),
            remember_default=username_salvato is not None,
            verify_callback=self._verify_login,
            username_default=username_salvato or "",
            password_default=password_salvata or "",
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

    def _handle_aggiorna_cantieri(self):
        """
        "⟳ Aggiorna": rilegge l'elenco cantieri dal server. Serve perche'
        populate_cantieri viene chiamato solo al login (_dopo_login_riuscito)
        -- un cantiere creato nel frattempo (app web o un'altra sessione
        QGIS) non compare finche' non si forza questo ricaricamento.
        """
        try:
            cantieri = self.matrice_api.list_cantieri_abilitati(self.session.access_token)
        except Exception as exc:
            self.dock_widget.show_error(str(exc))
            return
        self.dock_widget.populate_cantieri(cantieri)

    def _handle_nuovo_cantiere(self, tipo_preselezionato=None, precompilati=None, progettazione_origine_id=None):
        """
        Sezione 4, "Crea cantiere da QGIS": apre il dialog, risolve
        referente e azienda, inserisce la riga su Matrice e apre subito
        la tavola appena creata -- stesso punto di arrivo di un cantiere
        scelto dall'elenco (handle_open_tavola).
        """
        token = self.session.access_token
        try:
            tipi_consentiti = self.matrice_api.tipi_intervento_consentiti(token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Matrice", str(exc))
            return
        if not tipi_consentiti:
            self.iface.messageBar().pushWarning(
                "Matrice",
                "Il profilo collegato non e' abilitato a nessun tipo di lavoro: "
                "contattare il referente dell'azienda su Matrice.",
            )
            return

        if tipo_preselezionato and tipo_preselezionato not in [t[0] for t in tipi_consentiti]:
            self.iface.messageBar().pushWarning(
                "Matrice",
                "Il tuo profilo non è abilitato a questo tipo di lavoro: contattare il referente dell'azienda su Matrice.",
            )
            return

        dialog = NuovoCantiereDialog(
            tipi_consentiti, self.iface.mainWindow(),
            tipo_preselezionato=tipo_preselezionato, precompilati=precompilati,
        )
        if not dialog.exec_():
            return

        valori = dialog.valori()

        email_referente = valori["email_referente"]
        if not email_referente:
            referente_campo_utente_id = self.session.user_id
        else:
            referente = self.matrice_api.trova_utente_per_email(email_referente, token)
            if referente is not None:
                referente_campo_utente_id = referente["id"]
            else:
                referente_campo_utente_id = self.session.user_id
                self.iface.messageBar().pushWarning(
                    "Matrice",
                    "Nessun utente trovato con questa email: il referente di campo e' stato impostato "
                    "sull'utente attualmente connesso.",
                )

        try:
            azienda_id = self.matrice_api.azienda_utente_loggato(token)
            campi = {
                "nome_cantiere": valori["nome_cantiere"],
                "tipo_intervento": valori["tipo_intervento"],
                "comune": valori["comune"],
                "provincia": valori["provincia"],
                "regione": valori["regione"],
                "anno_esecuzione": valori["anno_esecuzione"],
                "referente_campo_utente_id": referente_campo_utente_id,
                "azienda_id": azienda_id,
                "created_by": self.session.user_id,
            }
            if progettazione_origine_id and valori["tipo_intervento"] in RIFERIMENTI_PER_TIPO_INTERVENTO:
                campi["progettazione_origine_id"] = progettazione_origine_id
            nuovo = self.matrice_api.crea_cantiere(campi, token)
            # has_cantiere_access() (RLS su `cantiere`) mostra la riga solo
            # a chi ha ruolo "referente" nella stessa azienda, oppure una
            # riga esplicita in cantiere_accesso: chi crea il cantiere non
            # e' detto che sia "referente" (puo' essere un "collaboratore"),
            # quindi senza questa riga il cantiere resterebbe invisibile
            # anche a chi l'ha appena creato. Va bene comunque inserirla
            # anche per un "referente" (vede gia' il cantiere lo stesso,
            # la riga in piu' non cambia nulla).
            self.matrice_api.rest.insert(
                "cantiere_accesso",
                {
                    "cantiere_id": nuovo["id"],
                    "utente_id": self.session.user_id,
                    "ruolo_accesso": "editor",
                },
                token,
            )
            # Se il referente di campo indicato e' un utente diverso da chi
            # crea il cantiere, serve la stessa riga anche per lui: non ha
            # accesso automatico solo perche' e' indicato in
            # referente_campo_utente_id (stessa regola della web app, vedi
            # dashboard.js `nuovoCantiere`).
            if referente_campo_utente_id != self.session.user_id:
                self.matrice_api.rest.insert(
                    "cantiere_accesso",
                    {
                        "cantiere_id": nuovo["id"],
                        "utente_id": referente_campo_utente_id,
                        "ruolo_accesso": "editor",
                    },
                    token,
                )
        except Exception as exc:
            self.iface.messageBar().pushCritical("Matrice", str(exc))
            return

        self.handle_open_tavola(nuovo["id"], nuovo["nome_cantiere"])
        self.dock_widget.populate_cantieri(self.matrice_api.list_cantieri_abilitati(token))

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
        self.progettazione_action.setEnabled(False)

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

        Il bottone "Progettazione" della toolbar si abilita qui, non in base
        al layer attivo in QGIS (bug segnalato 2026-10-17: l'abilitazione
        legata al layer attivo richiedeva anche un click sul nome del layer
        nel pannello "Livelli" nativo di QGIS, un passaggio in piu' non
        spiegato da nessuna parte che lasciava il bottone grigio senza
        alcuna indicazione del perche'). Da qui in avanti basta che la
        tavola di un cantiere sia aperta: ogni singola voce del menu
        (Trincea/Saggio/UR/Carotaggio) controlla comunque da
        sola, al momento del click, di avere il livello giusto a disposizione.
        """
        self.progettazione_action.setEnabled(True)
        self._carica_stato_progettazione(cantiere_id)
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

        # CRS di progetto sul sistema di riferimento personale dell'utente
        # loggato (comodo di visualizzazione/digitazione in QGIS): le
        # geometrie restano sempre salvate in EPSG:4326, questo non le
        # tocca. Mai bloccante.
        try:
            sr_id = self.matrice_api.sistema_riferimento_utente_loggato(self.session.access_token)
            epsg = SISTEMI_RIFERIMENTO_EPSG.get(sr_id, 4326)
            QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(f"EPSG:{epsg}"))
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
        if localizzazione and localizzazione.get("area_buffer_geom_wkt"):
            self.localizzazione_manager.layer_buffer(
                cantiere_id, nome_cantiere, localizzazione["area_buffer_geom_wkt"]
            )

        for key, layer in layers.items():
            if has_data[key]:
                self._watch_layer_sync(key, layer, cantiere_id)

        # Cantieri a valle (carotaggi / sorveglianza): elementi di progetto in sola lettura.
        self._carica_riferimenti_progettazione(cantiere_id)

        layer_states = [
            (key, opts["display_name"], has_data[key], _GRUPPO_LIVELLI.get(key))
            for key, opts in LAYER_SOURCES.items()
        ]
        # "Localizzazione di cantiere" e "Area di cantiere" non sono layer
        # PostGIS come gli altri (vedi core/localizzazione_manager.py): due
        # voci distinte, ciascuna spuntata solo se il relativo dato era gia'
        # presente su Matrice all'apertura (coerente con ha_punto/ha_area
        # appena calcolati, che hanno gia' deciso se crearli in mappa).
        layer_states.append(("localizzazione_cantiere", "Localizzazione di cantiere", ha_punto, None))
        layer_states.append(("area_intervento", "Area di cantiere", ha_area, None))
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

        # Vista sul cantiere (core/vista_cantiere.py): senza, con i livelli di
        # Progettazione quasi tutti vuoti, la mappa restava sul mondo intero e
        # i click per posizionare cadevano fuori dalla proiezione.
        self._zoom_su_cantiere(cantiere_id, localizzazione)

        # Stato iniziale del pannello "US attive" per questo cantiere (il
        # layer attivo in QGIS potrebbe non essere cambiato dall'apertura
        # della tavola precedente, quindi currentLayerChanged non scatta da
        # solo -- va valutato subito anche qui).
        self._handle_active_layer_changed(self.iface.activeLayer())

    # ------------------------------------------------------------------
    # Chiusura / stand-by della Progettazione
    # ------------------------------------------------------------------
    def _carica_stato_progettazione(self, cantiere_id):
        """Legge tipo e stato del cantiere aperto e aggiorna le voci del menu. Mai bloccante."""
        try:
            tipo, stato, origine = self.matrice_api.get_stato_progettazione(cantiere_id, self.session.access_token)
        except Exception:
            tipo, stato, origine = None, None, None
        self._tipo_intervento_corrente = tipo
        self._stato_progettazione = stato
        self._progettazione_origine_id = origine
        self._aggiorna_menu_progettazione()

    def _carica_riferimenti_progettazione(self, cantiere_id):
        """
        Nei cantieri a valle collegati a una Progettazione mostra in sola
        lettura i suoi elementi: carotaggi previsti (Lettura carotaggi),
        trincee e saggi di progetto (Assistenza in corso d'opera). Mai
        bloccante.
        """
        chiavi = RIFERIMENTI_PER_TIPO_INTERVENTO.get(self._tipo_intervento_corrente)
        if not chiavi or not self._progettazione_origine_id:
            return
        try:
            layers = self.layer_manager.carica_riferimento(cantiere_id, self._progettazione_origine_id, chiavi)
        except Exception as exc:
            self.iface.messageBar().pushWarning("Progettazione", f"Elementi di progetto non caricati: {exc}")
            return
        vuoti = all(layer.featureCount() == 0 for layer in layers)
        nome = self.matrice_api.nome_cantiere(self._progettazione_origine_id, self.session.access_token) or "progettazione"
        self.iface.messageBar().pushInfo(
            "Progettazione",
            f"Nessun elemento previsto nella progettazione «{nome}»." if vuoti
            else f"Mostrati in sola lettura gli elementi previsti dalla progettazione «{nome}».",
        )

    def _progettazione_chiusa(self):
        return self._stato_progettazione == "completato"

    def _aggiorna_menu_progettazione(self):
        chiusa = self._progettazione_chiusa()
        tipo = self._tipo_intervento_corrente
        e_progettazione = tipo == "progettazione"
        # Creare/spostare elementi di progetto solo in un cantiere di
        # Progettazione aperto (tipo sconosciuto = comportamento precedente).
        modificabile = tipo in (None, "progettazione") and not chiusa
        for azione in self._azioni_modifica_progettazione:
            azione.setEnabled(modificabile)
        self.azione_chiudi_progettazione.setVisible(e_progettazione and not chiusa)
        self.azione_riapri_progettazione.setVisible(e_progettazione and chiusa)
        self.azione_avvia_carotaggi.setVisible(e_progettazione and chiusa)
        self.azione_avvia_sorveglianza.setVisible(e_progettazione and chiusa)
        self.azione_collega_progettazione.setVisible(tipo in RIFERIMENTI_PER_TIPO_INTERVENTO)

    def _progettazione_modificabile(self):
        """False (con avviso chiaro) se la progettazione e' stata chiusa."""
        if self._progettazione_chiusa():
            QMessageBox.information(
                self.iface.mainWindow(), "Progettazione",
                "La progettazione di questo cantiere è chiusa (in stand-by).\n"
                "Per modificarla scegli «Riapri progettazione» dal menu Progettazione.",
            )
            return False
        return True

    def _chiudi_progettazione(self):
        """
        «Chiudi progettazione»: segna la progettazione come completata (resta
        in stand-by, riapribile) e propone di avviare il flusso «Lettura
        carotaggi» come cantiere a parte. I dati di progetto non si toccano.
        """
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            return
        risposta = QMessageBox.question(
            self.iface.mainWindow(), "Chiudi progettazione",
            "Chiudere la progettazione di questo cantiere?\n\n"
            "Trincee, saggi, UR e carotaggi di progetto restano salvati ma non "
            "saranno più modificabili da QGIS finché non la riapri.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if risposta != QMessageBox.Yes:
            return
        try:
            riga = self.matrice_api.set_stato_progettazione(cantiere_id, "completato", self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        if riga is None:
            self.iface.messageBar().pushWarning(
                "Progettazione",
                "Chiusura non riuscita: serve il ruolo «referente» sul cantiere. Contatta il referente dell'azienda.",
            )
            return
        self._stato_progettazione = "completato"
        self._aggiorna_menu_progettazione()
        strumento_attivo = self.iface.mapCanvas().mapTool()
        if isinstance(strumento_attivo, (PosizionaRettangoloMapTool, PosizionaPuntoMapTool)):
            self.iface.mapCanvas().unsetMapTool(strumento_attivo)
        self.iface.messageBar().pushSuccess("Progettazione", "Progettazione chiusa: il cantiere resta in stand-by.")

        scelta = QMessageBox(self.iface.mainWindow())
        scelta.setWindowTitle("Prossimo flusso di lavoro")
        scelta.setText(
            "Progettazione chiusa. Quale flusso di lavoro vuoi avviare ora?\n\n"
            "• Lettura carotaggi: legge i carotaggi previsti e li mostra in mappa.\n"
            "• Assistenza in corso d'opera: mostra in mappa trincee e saggi di progetto.\n\n"
            "Puoi avviare l'altro in qualsiasi momento dal menu Progettazione."
        )
        pulsante_carotaggi = scelta.addButton("Lettura carotaggi", QMessageBox.AcceptRole)
        pulsante_sorveglianza = scelta.addButton("Assistenza in corso d'opera", QMessageBox.AcceptRole)
        scelta.addButton("Non ora", QMessageBox.RejectRole)
        scelta.exec_()
        if scelta.clickedButton() == pulsante_carotaggi:
            self._avvia_flusso_da_progettazione("carotaggi")
        elif scelta.clickedButton() == pulsante_sorveglianza:
            self._avvia_flusso_da_progettazione("sorveglianza")

    def _avvia_flusso_da_progettazione(self, tipo):
        """
        Crea il cantiere a valle ('carotaggi' = Lettura carotaggi,
        'sorveglianza' = Assistenza in corso d'opera) collegato alla
        Progettazione aperta (progettazione_origine_id): il nuovo cantiere
        mostra in sola lettura i suoi carotaggi / trincee / saggi.
        """
        origine_id = self.session.cantiere_corrente_id
        if not origine_id:
            return
        try:
            base = self.matrice_api.rest.select(
                "cantiere", self.session.access_token,
                params={"id": f"eq.{origine_id}", "select": "nome_cantiere,comune,provincia,regione"},
            )
            base = base[0] if base else {}
        except Exception:
            base = {}
        suffisso = "Lettura carotaggi" if tipo == "carotaggi" else "Assistenza in corso d'opera"
        precompilati = dict(base)
        if base.get("nome_cantiere"):
            precompilati["nome_cantiere"] = f"{base['nome_cantiere']} - {suffisso}"
        self._handle_nuovo_cantiere(
            tipo_preselezionato=tipo, precompilati=precompilati, progettazione_origine_id=origine_id
        )

    def _collega_progettazione(self):
        """Per cantieri carotaggi/sorveglianza gia' esistenti: sceglie la Progettazione di cui mostrare gli elementi."""
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            return
        try:
            progettazioni = self.matrice_api.list_progettazioni(self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        if not progettazioni:
            QMessageBox.information(self.iface.mainWindow(), "Progettazione", "Non ci sono cantieri di Progettazione disponibili.")
            return
        nomi = [p["nome_cantiere"] for p in progettazioni]
        nome, ok = QInputDialog.getItem(
            self.iface.mainWindow(), "Collega progettazione",
            "Progettazione di cui mostrare gli elementi previsti:", nomi, 0, False,
        )
        if not ok:
            return
        origine = progettazioni[nomi.index(nome)]["id"]
        try:
            riga = self.matrice_api.collega_progettazione(cantiere_id, origine, self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        if riga is None:
            self.iface.messageBar().pushWarning("Progettazione", "Collegamento non riuscito: serve il ruolo «referente» sul cantiere.")
            return
        self._progettazione_origine_id = origine
        self._carica_riferimenti_progettazione(cantiere_id)
        self._zoom_su_cantiere(cantiere_id)

    def _riapri_progettazione(self):
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            return
        try:
            riga = self.matrice_api.set_stato_progettazione(cantiere_id, "in_corso", self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        if riga is None:
            self.iface.messageBar().pushWarning(
                "Progettazione", "Riapertura non riuscita: serve il ruolo «referente» sul cantiere."
            )
            return
        self._stato_progettazione = "in_corso"
        self._aggiorna_menu_progettazione()
        self.iface.messageBar().pushSuccess("Progettazione", "Progettazione riaperta.")

    def _zoom_su_cantiere(self, cantiere_id, localizzazione=None):
        """
        Ingrandisce la mappa su quanto il cantiere ha gia' (punto di
        localizzazione, area disegnata, livelli con dati). Ritorna True se ha
        potuto farlo. Mai bloccante.
        """
        if localizzazione is None:
            try:
                localizzazione = self.matrice_api.get_localizzazione_cantiere(cantiere_id, self.session.access_token)
            except Exception:
                localizzazione = None
        layers_con_dati = []
        for key in self.layer_manager.current_layers(cantiere_id):
            try:
                # Solo i livelli davvero in mappa e con almeno una riga.
                if not self.layer_manager.is_layer_shown(cantiere_id, key):
                    continue
                layer = self.layer_manager.current_layers(cantiere_id)[key]
                if layer.featureCount() > 0:
                    layers_con_dati.append(layer)
            except Exception:
                continue
        for layer in self.layer_manager.layers_riferimento(cantiere_id):
            try:
                if layer.featureCount() > 0:
                    layers_con_dati.append(layer)
            except Exception:
                continue
        estensione = estensione_cantiere_wgs84(layers_con_dati, localizzazione)
        if estensione is None:
            return False
        return zoom_su_estensione_wgs84(self.iface.mapCanvas(), estensione)

    def _assicura_vista_valida(self, cantiere_id):
        """
        Prima di attivare uno strumento "clicca per posizionare": se la vista
        e' troppo ampia (es. mondo intero) la riporta sul cantiere; se non
        e' possibile avvisa l'operatore. Ritorna True se si puo' procedere.
        """
        canvas = self.iface.mapCanvas()
        if vista_canvas_valida(canvas):
            return True
        if self._zoom_su_cantiere(cantiere_id) and vista_canvas_valida(canvas):
            return True
        self._avviso_click_fuori_dominio()
        return False

    def _fine_posizionamento(self, creati, nome):
        """Riepilogo a fine posizionamento continuo (Esc o tasto destro)."""
        if creati:
            self.iface.messageBar().pushSuccess(
                "Progettazione", f"Posizionamento terminato: {creati} elementi ({nome}) salvati su Matrice."
                if creati > 1 else f"Posizionamento terminato: 1 elemento ({nome}) salvato su Matrice."
            )
        else:
            self.iface.messageBar().pushInfo("Progettazione", "Posizionamento annullato: nessun elemento creato.")

    def _avviso_click_fuori_dominio(self):
        self.iface.messageBar().pushWarning(
            "Progettazione",
            "La mappa e' troppo ingrandita sul mondo intero: ingrandisci sulla zona del cantiere "
            "(rotella del mouse) e clicca di nuovo.",
        )

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
        # NOTA (bug segnalato 2026-10-17): il bottone "Progettazione" NON si
        # abilita piu' in base al layer attivo in QGIS. Quel meccanismo
        # (uguale a "US attive" sopra) richiedeva che l'operatore cliccasse
        # anche sul nome del layer nel pannello "Livelli" NATIVO di QGIS,
        # oltre a spuntarlo nel pannello Matrice -- un passaggio in piu' non
        # spiegato da nessuna parte, che lasciava il bottone grigio senza
        # alcuna indicazione del perche'. Ogni singola voce del menu
        # (Trincea/Saggio/UR/Carotaggio) verifica comunque da
        # sola, al momento del click, che la tavola di un cantiere sia
        # aperta (altrimenti avvisa l'operatore) -- quindi non serve
        # replicare qui lo stesso controllo in anticipo: e' sufficiente che
        # sia aperta la tavola, vedi handle_open_tavola/handle_logout.

    def _chiave_layer_attivo(self, cantiere_id, layer):
        """Chiave in LAYER_SOURCES del layer QGIS attivo, o None se non e' uno dei layer di Matrice."""
        if cantiere_id is None or layer is None:
            return None
        for key, candidato in self.layer_manager.current_layers(cantiere_id).items():
            if candidato.id() == layer.id():
                return key
        return None

    def _apri_nuovo_rettangolo(self, tipo):
        """
        tipo: 'trincea' o 'saggio' (le due voci del menu "Progettazione",
        stessa funzione, scheda diversa). Apre il dialog con i campi del
        tipo scelto; alla conferma attiva lo strumento "un click per
        posizionare" (core/rettangolo_progettazione.py) sul layer
        corrispondente, SEMPRE quello scelto nel menu, non necessariamente
        quello attivo in quel momento in QGIS.
        """
        cantiere_id = self.session.cantiere_corrente_id
        chiave = "trincea_progettazione" if tipo == "trincea" else "saggio_progettazione"
        if not cantiere_id:
            QMessageBox.warning(self.iface.mainWindow(), "Progettazione", "Apri prima la tavola di un cantiere di Progettazione.")
            return
        # show_layer (non current_layers): se il livello e' ancora vuoto e non
        # spuntato non e' in mappa, e il rettangolo creato resterebbe
        # invisibile e fuori dalla sincronizzazione.
        layer, era_gia_visibile = self.layer_manager.show_layer(cantiere_id, chiave)
        if not era_gia_visibile:
            self._watch_layer_sync(chiave, layer, cantiere_id)

        dialog = RettangoloProgettazioneDialog(tipo, self.iface.mainWindow())
        if dialog.exec_() != dialog.Accepted:
            return
        valori = dialog.valori()
        # larghezza_m/lunghezza_m servono sia a costruire il rettangolo sia
        # come colonne NOT NULL della tabella (bug 2026-10-17: prima venivano
        # tolte dai valori e l'insert falliva): si leggono senza rimuoverle.
        larghezza_m = valori["larghezza_m"]
        lunghezza_m = valori["lunghezza_m"]
        # Obbligatori sulla tabella e non ereditati dalla vista QGIS.
        valori["cantiere_id"] = cantiere_id
        valori["created_by"] = self.session.user_id

        def dopo_creazione(ok):
            if ok:
                layer.triggerRepaint()
            else:
                self.iface.messageBar().pushWarning("Progettazione", "Creazione non riuscita.")

        if not self._assicura_vista_valida(cantiere_id):
            return
        strumento = PosizionaRettangoloMapTool(
            self.iface.mapCanvas(), layer, larghezza_m, lunghezza_m, valori, dopo_creazione,
            on_errore=self._avviso_click_fuori_dominio,
            on_terminato=lambda n: self._fine_posizionamento(n, tipo),
        )
        self.iface.messageBar().pushInfo(
            "Progettazione",
            f"Clicca sulla mappa per posizionare {'la trincea' if tipo == 'trincea' else 'il saggio'}; "
            "ogni click ne aggiunge uno uguale. Tasto destro o Esc per finire.",
        )
        self.iface.mapCanvas().setMapTool(strumento)

    def _avvia_strumento_nativo(self, nome_oggetto, nome_iface, descrizione):
        """
        Avvia uno strumento di modifica nativo QGIS (Ruota/Sposta elementi)
        sul layer attivo, mettendolo in modifica se serve.

        QgisInterface NON espone actionRotateFeature (bug 2026-10-17:
        AttributeError): lo strumento esiste solo come QAction della
        finestra principale, cercata per nome oggetto. Per Sposta basta
        iface.actionMoveFeature(), ma si usa lo stesso percorso per
        uniformita' e per sopravvivere a differenze tra versioni di QGIS.
        """
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "startEditing"):
            self.iface.messageBar().pushWarning(
                "Progettazione", f"Per {descrizione} seleziona prima nel pannello Livelli il livello dell'elemento."
            )
            return
        azione = None
        if nome_iface and hasattr(self.iface, nome_iface):
            azione = getattr(self.iface, nome_iface)()
        if azione is None:
            azione = self.iface.mainWindow().findChild(QAction, nome_oggetto)
        if azione is None:
            self.iface.messageBar().pushWarning(
                "Progettazione",
                f"Strumento «{descrizione}» non trovato in questa versione di QGIS: usalo dalla barra "
                "«Digitalizzazione avanzata» (menu Visualizza > Barre degli strumenti).",
            )
            return
        if not layer.isEditable():
            layer.startEditing()
        azione.trigger()
        self.iface.messageBar().pushInfo(
            "Progettazione", f"{descrizione.capitalize()}: clicca l'elemento sulla mappa, poi salva le modifiche del livello."
        )

    def _ruota_elemento_progettazione(self):
        """Strumento nativo QGIS "Ruota elementi" (vedi nota in rettangolo_progettazione.py sull'angolo)."""
        self._avvia_strumento_nativo("mActionRotateFeature", None, "ruotare l'elemento")

    def _sposta_elemento_progettazione(self):
        """Strumento nativo QGIS "Sposta elementi"."""
        self._avvia_strumento_nativo("mActionMoveFeature", "actionMoveFeature", "spostare l'elemento")

    def _apri_nuovo_carotaggio(self):
        """
        Apre il dialog "Nuovo carotaggio" e, alla conferma, attiva lo stesso
        strumento "un click per posizionare" usato per il rinvenimento
        (PosizionaPuntoMapTool, core/ritrovamento_progettazione.py -- e'
        generico, non specifico al rinvenimento nonostante il nome del
        modulo). numero_carotaggio/lat/lng li calcola il trigger INSTEAD OF
        INSERT di v_carotaggio_qgis (sezione dedicata della spec import
        dati), qui si passano solo i campi compilabili sul campo.

        Il layer si prende via show_layer (non current_layers): un cantiere
        senza ancora alcun carotaggio potrebbe non averlo mai mostrato in
        mappa (sezione 4, passo 1 -- livelli senza dati non caricati in
        automatico), show_layer lo crea/mostra vuoto e pronto per addFeature,
        stesso schema di _apri_nuovo_rettangolo.
        """
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            QMessageBox.warning(self.iface.mainWindow(), "Progettazione", "Apri prima la tavola di un cantiere.")
            return
        if not self._progettazione_modificabile():
            return
        # Layer proprio della Progettazione (tabella carotaggio_progettazione),
        # separato dai carotaggi della "Lettura carotaggi".
        layer, era_gia_visibile = self.layer_manager.show_layer(cantiere_id, "carotaggio_progettazione")
        if not era_gia_visibile:
            self._watch_layer_sync("carotaggio_progettazione", layer, cantiere_id)

        dialog = CarotaggioProgettazioneDialog(self.iface.mainWindow())
        if dialog.exec_() != dialog.Accepted:
            return
        attributi = dialog.valori()
        # cantiere_id e' obbligatorio: il trigger INSTEAD OF INSERT di
        # v_carotaggio_qgis lo usa sia per la riga vera e propria sia per
        # calcolare il prossimo numero_carotaggio (max+1 filtrato su questo
        # cantiere) -- senza, l'insert fallirebbe sul vincolo NOT NULL
        # (obbligatorio come cantiere_id per trincea/saggio). Il codice
        # (CAR-01, CAR-02...) lo assegna il database.
        attributi["cantiere_id"] = cantiere_id
        attributi["created_by"] = self.session.user_id

        def dopo_creazione(ok):
            if ok:
                layer.triggerRepaint()
            else:
                self.iface.messageBar().pushWarning("Progettazione", "Creazione non riuscita.")

        if not self._assicura_vista_valida(cantiere_id):
            return
        strumento = PosizionaPuntoMapTool(
            self.iface.mapCanvas(), layer, attributi, dopo_creazione, on_errore=self._avviso_click_fuori_dominio,
            on_terminato=lambda n: self._fine_posizionamento(n, "carotaggio di progetto"),
        )
        self.iface.messageBar().pushInfo(
            "Progettazione",
            "Clicca sulla mappa per posizionare il carotaggio; ogni click ne aggiunge uno uguale. Tasto destro o Esc per finire.",
        )
        self.iface.mapCanvas().setMapTool(strumento)

    def _disegna_unita_ricognizione(self):
        """
        Attiva l'editing nativo QGIS sul layer survey_ur (poligono libero,
        niente tool custom come il rettangolo di trincea/saggio: il form
        attributi di QGIS si apre da solo a disegno completato, dove
        l'operatore compila codice_ur -- obbligatorio -- e gli altri campi).
        """
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            QMessageBox.warning(self.iface.mainWindow(), "Progettazione", "Apri prima la tavola di un cantiere di Progettazione.")
            return
        layer, _ = self.layer_manager.show_layer(cantiere_id, "survey_ur")
        # Il form nativo QGIS non conosce il cantiere: senza un valore
        # predefinito, cantiere_id (NOT NULL) arriverebbe vuoto e il
        # salvataggio fallirebbe. created_by e' facoltativo ma utile.
        for campo, valore in (("cantiere_id", cantiere_id), ("created_by", self.session.user_id)):
            indice = layer.fields().indexOf(campo)
            if indice >= 0 and valore:
                layer.setDefaultValueDefinition(indice, QgsDefaultValue(f"'{valore}'"))
        self.iface.setActiveLayer(layer)
        if not layer.isEditable():
            layer.startEditing()
        self.iface.actionAddFeature().trigger()

    def _crea_buffer_area(self):
        """
        Genera un buffer attorno all'area di cantiere gia' caricata
        (cantiere.area_geom_wkt) e lo salva su cantiere.area_buffer_geom_wkt
        -- distanza chiesta all'operatore ad ogni generazione, non fissa sul
        cantiere. Le UR si disegnano su questo buffer (guida visiva, nessun
        vincolo geometrico imposto dal codice).
        """
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            QMessageBox.warning(self.iface.mainWindow(), "Progettazione", "Apri prima la tavola di un cantiere di Progettazione.")
            return
        try:
            localizzazione = self.matrice_api.get_localizzazione_cantiere(cantiere_id, self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        area_wkt = localizzazione.get("area_geom_wkt") if localizzazione else None
        if not area_wkt:
            QMessageBox.warning(self.iface.mainWindow(), "Progettazione", "Carica prima l'area di cantiere (Mappa di cantiere) prima di generare il buffer.")
            return
        dialog = BufferAreaDialog(self.iface.mainWindow())
        if dialog.exec_() != dialog.Accepted:
            return
        distanza_m = dialog.valori()["distanza_m"]
        try:
            wkt_buffer = calcola_buffer_wkt(area_wkt, distanza_m)
            self.matrice_api.update_area_buffer(cantiere_id, wkt_buffer, self.session.access_token)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Progettazione", str(exc))
            return
        nome_cantiere = self.layer_manager.nome_cantiere(cantiere_id) or cantiere_id
        self.localizzazione_manager.layer_buffer(cantiere_id, nome_cantiere, wkt_buffer)
        self.iface.messageBar().pushSuccess("Progettazione", f"Buffer di {distanza_m} m creato e salvato.")

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
            if localizzazione.get("area_buffer_geom_wkt"):
                self.localizzazione_manager.layer_buffer(
                    cantiere_id, nome_cantiere, localizzazione["area_buffer_geom_wkt"]
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

    def _handle_importa_area_da_file(self):
        """
        Azione toolbar "Importa area da file…" (sezione 4): alternativa al
        disegno a mano del poligono "Area di cantiere" -- legge la prima
        geometria di uno shapefile/GeoPackage/KML e la salva su Matrice,
        poi ricarica il layer "Area di cantiere (disegno)" in mappa per
        mostrare subito il poligono importato (stesso stile applicato dopo
        un disegno a mano o all'apertura tavola, vedi _apply_area_style).
        """
        cantiere_id = self.session.cantiere_corrente_id
        if not cantiere_id:
            self._show_warning("Aprire prima la tavola di un cantiere.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Importa area di cantiere",
            "",
            "Vettoriali (*.shp *.gpkg *.kml)",
        )
        if not path:
            return

        try:
            wkt = self.localizzazione_manager.wkt_da_file(path)
        except ValueError as exc:
            self.iface.messageBar().pushWarning("Matrice", str(exc))
            return

        try:
            self.matrice_api.update_area_intervento(cantiere_id, wkt, self.session.access_token)
        except Exception as exc:
            self._show_warning(str(exc))
            return

        nome_cantiere = self.layer_manager.nome_cantiere(cantiere_id) or cantiere_id
        layer = self.localizzazione_manager.layer_area(cantiere_id, nome_cantiere, wkt)
        layer.dataProvider().truncate()
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        layer.triggerRepaint()
        self._apply_area_style(layer)
        self.iface.messageBar().pushSuccess("Matrice", "Area dell'intervento importata e salvata.")

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
