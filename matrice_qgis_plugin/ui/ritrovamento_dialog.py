"""
Dialog "Nuovo rinvenimento" (menu Progettazione, matrice_plugin.py): raccoglie
la UR di appartenenza (obbligatoria, `survey_ritrovamento.ur_id` e' NOT NULL
con unique(ur_id, numero_progressivo) -- verificato in produzione, non era
nello spec originale) + materiale/quantita/stato di conservazione/descrizione.
Alla conferma NON crea ancora il punto: restituisce solo i valori, la
creazione (click sulla mappa) la fa PosizionaPuntoMapTool dopo che il dialog
si chiude (core/ritrovamento_progettazione.py).

`numero_progressivo` non e' un campo del dialog: lo calcola un trigger
database (max+1 per quella UR), mai passato da qui.
"""

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

STATO_CONSERVAZIONE = [
    ("intero", "Intero"),
    ("frammentario", "Frammentario"),
    ("molto_frammentario", "Molto frammentario"),
]


class RitrovamentoDialog(QDialog):
    def __init__(self, ur_disponibili, parent=None):
        """ur_disponibili: lista di (id, codice_ur) delle UR gia' disegnate per il cantiere corrente."""
        super().__init__(parent)
        self.setWindowTitle("Nuovo rinvenimento")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Dopo «OK» clicca sulla mappa nel punto del rinvenimento."))
        form = QFormLayout()
        layout.addLayout(form)

        self.urInput = QComboBox()
        for ur_id, codice_ur in ur_disponibili:
            self.urInput.addItem(codice_ur, ur_id)
        form.addRow("Unità di Ricognizione", self.urInput)

        self.materialeInput = QLineEdit()
        form.addRow("Materiale", self.materialeInput)

        self.quantitaInput = QSpinBox()
        self.quantitaInput.setRange(0, 9999)
        self.quantitaInput.setValue(1)
        form.addRow("Quantità", self.quantitaInput)

        self.statoConservazioneInput = QComboBox()
        for valore, etichetta in STATO_CONSERVAZIONE:
            self.statoConservazioneInput.addItem(etichetta, valore)
        form.addRow("Stato di conservazione", self.statoConservazioneInput)

        self.descrizioneInput = QPlainTextEdit()
        self.descrizioneInput.setFixedHeight(60)
        form.addRow("Descrizione", self.descrizioneInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per `attributi` di crea_feature_ritrovamento. SENZA numero_progressivo (lo calcola il trigger)."""
        return {
            "ur_id": self.urInput.currentData(),
            "materiale": self.materialeInput.text().strip() or None,
            "quantita": self.quantitaInput.value(),
            "stato_conservazione": self.statoConservazioneInput.currentData(),
            "descrizione": self.descrizioneInput.toPlainText().strip() or None,
        }
