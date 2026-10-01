"""
Pannello principale del plugin (sezioni 2, 4, 6, 8): elenco cantieri
abilitati, fasi, US attive/non attive, vista rapida. Interfaccia ridotta
al minimo per un operatore poco pratico di informatica: nessun campo
tecnico, un solo modo per fare ogni azione.
"""

import os

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QDockWidget, QListWidgetItem

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), "main_dock.ui"))


class MainDockWidget(QDockWidget):
    logout_requested = pyqtSignal()
    cantiere_selected = pyqtSignal(str, str)  # cantiere.id (uuid), nome_cantiere
    quick_view_requested = pyqtSignal(str, str)
    gps_import_requested = pyqtSignal(str)  # cantiere.id del rilievo aperto
    layer_visibility_toggled = pyqtSignal(str, str, bool)  # cantiere_id, layer_key, mostra/nascondi

    def __init__(self, parent=None):
        super().__init__("Matrice", parent)
        self._content = uic.loadUi(os.path.join(os.path.dirname(__file__), "main_dock.ui"))
        self.setWidget(self._content)
        self._cantiere_corrente_id = None  # per sapere a quale cantiere riferire i toggle dei livelli

        self._content.errorLabel.setText("")
        self._content.apriTavolaButton.clicked.connect(self._on_apri_tavola)
        self._content.applyQuickViewButton.clicked.connect(self._on_apply_quick_view)
        self._content.importaGpsButton.clicked.connect(self._on_importa_gps)
        self._content.logoutButton.clicked.connect(self.logout_requested.emit)
        self._content.layerToggleListWidget.itemChanged.connect(self._on_layer_toggle_changed)

    # ------------------------------------------------------------------
    # Popolamento da Matrice (sola lettura filtrata sui permessi utente)
    # ------------------------------------------------------------------
    def populate_cantieri(self, cantieri):
        """cantieri: [{"id": uuid, "nome_cantiere": str}, ...] (MatriceConnection.list_cantieri_abilitati)."""
        combo = self._content.cantiereCombo
        combo.clear()
        for c in cantieri:
            combo.addItem(c["nome_cantiere"], c["id"])

    def populate_fasi(self, fasi):
        """
        fasi: [{"fase": str}, ...] (MatriceConnection.list_fasi).

        `fase` e' un campo testuale libero sulla scheda US, non una tabella
        con colore/numero propri: l'elenco mostra solo la denominazione.
        """
        list_widget = self._content.fasiListWidget
        list_widget.clear()
        for fase in fasi:
            item = QListWidgetItem(fase["fase"])
            item.setData(Qt.UserRole, fase["fase"])
            item.setCheckState(Qt.Checked)
            list_widget.addItem(item)

    def set_us_attive_visibile(self, visibile):
        """
        "US attive" ha senso solo mentre si lavora sul layer "Rilievo
        vettoriale" (e' l'elenco per l'associazione geometria-US, sezione
        8): nascosto quando e' attivo un altro layer, per non confondere
        l'operatore con un pannello che in quel momento non significa nulla.
        """
        self._content.usAttiveLabel.setVisible(visibile)
        self._content.usAttiveListWidget.setVisible(visibile)

    def populate_us_attive(self, us_rows):
        """
        us_rows: [{"id", "numero_us", "definizione", "fase", "attiva",
        "sepoltura_id"}, ...] (MatriceConnection.list_us, elenco leggero
        per l'associazione in QGIS -- sezione 8 e nota "elenco US").
        """
        list_widget = self._content.usAttiveListWidget
        list_widget.clear()
        for us in us_rows:
            item = QListWidgetItem(f"US {us['numero_us']} - {us['definizione'] or ''}".rstrip(" -"))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, us["id"])
            item.setCheckState(Qt.Checked if us["attiva"] else Qt.Unchecked)
            list_widget.addItem(item)

    def populate_layer_toggles(self, cantiere_id, layer_states):
        """
        layer_states: [(key, display_name, has_data), ...] -- uno per
        ciascun livello di LayerManager.LAYER_SOURCES. Spuntati quelli gia'
        in mappa (has_data=True, caricati automaticamente all'apertura
        tavola perche' contengono dati); gli altri restano disponibili a
        richiesta, spuntabili per aggiungerli comunque in mappa (vuoti,
        pronti per iniziare a digitalizzare).
        """
        self._cantiere_corrente_id = cantiere_id
        list_widget = self._content.layerToggleListWidget
        list_widget.blockSignals(True)
        list_widget.clear()
        for key, display_name, has_data in layer_states:
            item = QListWidgetItem(display_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, key)
            item.setCheckState(Qt.Checked if has_data else Qt.Unchecked)
            list_widget.addItem(item)
        list_widget.blockSignals(False)

    def _on_layer_toggle_changed(self, item):
        if self._cantiere_corrente_id is None:
            return
        key = item.data(Qt.UserRole)
        self.layer_visibility_toggled.emit(self._cantiere_corrente_id, key, item.checkState() == Qt.Checked)

    def populate_quick_views(self, views):
        """views: [(chiave, etichetta), ...] da QuickViewManager.available_views()."""
        combo = self._content.quickViewCombo
        combo.clear()
        for key, etichetta in views:
            combo.addItem(etichetta, key)

    # ------------------------------------------------------------------
    def show_error(self, message):
        self._content.errorLabel.setText(message)

    def show_warning(self, message):
        """
        Avviso non bloccante (es. quota non ricalcolata perche' manca il DEM
        del cantiere -- sezione 5/8): stesso punto dell'errore di login, ma
        senza interrompere l'operativita' dell'utente.
        """
        self._content.errorLabel.setText(message)

    def reset(self):
        self._content.cantiereCombo.clear()
        self._content.fasiListWidget.clear()
        self._content.usAttiveListWidget.clear()
        self.set_us_attive_visibile(False)
        self._content.layerToggleListWidget.clear()
        self._cantiere_corrente_id = None
        self._content.errorLabel.setText("")

    # ------------------------------------------------------------------
    def _on_apri_tavola(self):
        combo = self._content.cantiereCombo
        cantiere_id = combo.currentData()
        if cantiere_id is not None:
            self.cantiere_selected.emit(cantiere_id, combo.currentText())

    def _on_apply_quick_view(self):
        cantiere_id = self._content.cantiereCombo.currentData()
        view_key = self._content.quickViewCombo.currentData()
        if cantiere_id is not None and view_key is not None:
            self.quick_view_requested.emit(cantiere_id, view_key)

    def _on_importa_gps(self):
        cantiere_id = self._content.cantiereCombo.currentData()
        if cantiere_id is None:
            self.show_error("Selezionare prima un cantiere.")
            return
        self.gps_import_requested.emit(cantiere_id)
