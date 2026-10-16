"""
Dialog "Nuovo carotaggio" (menu Progettazione, matrice_plugin.py): raccoglie
localizzazione/diametro/profondita prevista/metodo di perforazione. Alla
conferma NON crea ancora il punto: restituisce solo i valori, la creazione
(click sulla mappa) la fa PosizionaPuntoMapTool dopo che il dialog si chiude
-- stesso schema di ui/ritrovamento_dialog.py.

numero_carotaggio NON e' un campo del dialog: lo calcola il trigger
database v_carotaggio_qgis_insert() (max+1 per cantiere_id), mai passato
da qui. Gli altri campi della scheda (esito, stratigrafia, falda, foto,
note) si compilano dopo, sul campo, con un doppio click sul punto creato
(ui/punto_dettaglio_dialog.py) -- qui si raccoglie solo cio' che si
decide in fase di progettazione, prima di andare a carotare.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)


class NuovoCarotaggioDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuovo carotaggio")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.localizzazioneInput = QLineEdit()
        form.addRow("Localizzazione", self.localizzazioneInput)

        self.diametroInput = QDoubleSpinBox()
        self.diametroInput.setRange(0, 999)
        self.diametroInput.setDecimals(1)
        self.diametroInput.setSuffix(" cm")
        form.addRow("Diametro", self.diametroInput)

        self.profonditaInput = QDoubleSpinBox()
        self.profonditaInput.setRange(0, 999)
        self.profonditaInput.setDecimals(2)
        self.profonditaInput.setSuffix(" m")
        form.addRow("Profondità prevista", self.profonditaInput)

        self.metodoInput = QLineEdit()
        form.addRow("Metodo di perforazione", self.metodoInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per `attributi` di crea_feature_ritrovamento (riusato per il carotaggio)."""
        return {
            "localizzazione": self.localizzazioneInput.text().strip() or None,
            "diametro": self.diametroInput.value() or None,
            "profondita": self.profonditaInput.value() or None,
            "metodo_perforazione": self.metodoInput.text().strip() or None,
        }
