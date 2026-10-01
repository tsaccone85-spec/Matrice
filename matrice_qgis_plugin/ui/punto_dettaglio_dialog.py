"""
Scheda di dettaglio/editing per un singolo elemento puntuale senza legame
con una US: colonna stratigrafica o carotaggio (stessa struttura --
foto_1_url/foto_2_url/foto_3_url, campi descrittivi -- vedi
core/matrice_api.py). Si apre con un doppio click su un elemento gia'
selezionato del layer corrispondente (vedi matrice_plugin.py), stesso
principio della scheda US (ui/us_detail_dialog.py) ma per un record
singolo, senza tab collegate: condivide con quella il form dinamico e il
caricamento foto autenticato da Storage (ui/scheda_comune.py).
"""

from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from .scheda_comune import DynamicRecordForm, SchedaConFotoBase, in_area_scorrevole

# jsonb (stratigrafia) escluso: non gestibile con un campo di testo
# semplice, stesso motivo per cui e' nascosto anche in tabella attributi
# (core/layer_manager.py). storage_provider e' un dettaglio tecnico interno.
CAMPI_ESCLUSI_COMUNI = {
    "id", "cantiere_id", "created_by", "created_at",
    "foto_1_url", "foto_2_url", "foto_3_url",
    "stratigrafia", "storage_provider",
}


class PuntoDettaglioDialog(QDialog, SchedaConFotoBase):
    def __init__(self, matrice_api, session, titolo, leggi, scrivi, record_id, parent=None):
        """
        titolo: titolo iniziale della finestra (es. "Colonna stratigrafica").
        leggi/scrivi: matrice_api.get_colonna_stratigrafica/update_colonna_stratigrafica
        (o le equivalenti per carotaggio) -- cosi' la finestra resta generica
        senza dover sapere su quale tabella sta lavorando.
        """
        super().__init__(parent)
        self.matrice_api = matrice_api
        self.session = session
        self._leggi = leggi
        self._scrivi = scrivi
        self.record_id = record_id
        self._form = None

        self.setWindowTitle(titolo)
        self.resize(480, 560)

        layout = QVBoxLayout(self)
        self.errorLabel = QLabel("")
        self.errorLabel.setStyleSheet("color: #b3261e;")
        self.errorLabel.setWordWrap(True)
        layout.addWidget(self.errorLabel)

        self._contenitore_layout = layout
        self._carica_dati(titolo)

        pulsanti = QHBoxLayout()
        pulsanti.addStretch()
        self.saveButton = QPushButton("Salva modifiche")
        self.saveButton.clicked.connect(self._salva)
        pulsanti.addWidget(self.saveButton)
        closeButton = QPushButton("Chiudi")
        closeButton.clicked.connect(self.accept)
        pulsanti.addWidget(closeButton)
        layout.addLayout(pulsanti)

    def _carica_dati(self, titolo_base):
        try:
            riga = self._leggi(self.record_id, self.session.access_token)
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            return
        if riga is None:
            self.errorLabel.setText(f"{titolo_base} non trovata/o (o non piu' accessibile).")
            return

        numero = riga.get("numero_colonna") or riga.get("numero_carotaggio")
        if numero is not None:
            self.setWindowTitle(f"{titolo_base} {numero}")

        foto_layout = QHBoxLayout()
        for campo_foto, etichetta in (("foto_1_url", "Foto 1"), ("foto_2_url", "Foto 2"), ("foto_3_url", "Foto 3")):
            if campo_foto in riga:
                foto_layout.addWidget(self._etichetta_foto(riga.get(campo_foto), etichetta))
        self._contenitore_layout.addLayout(foto_layout)

        self._form = DynamicRecordForm(riga, CAMPI_ESCLUSI_COMUNI)
        self._contenitore_layout.addWidget(in_area_scorrevole(self._form))

    def _salva(self):
        if self._form is None:
            return
        self.errorLabel.setText("")
        try:
            self._scrivi(self.record_id, self._form.valori_correnti(), self.session.access_token)
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            return
        QMessageBox.information(self, "Matrice", "Modifiche salvate.")
