"""
Dialog "Nuova trincea"/"Nuovo saggio" (menu Progettazione, matrice_plugin.py):
raccoglie nome/larghezza/lunghezza/profondita' + i campi propri del tipo
scelto (metodo+data per la trincea; stato+esito+apertura/chiusura per il
saggio). Stessa funzione per i due tipi, scheda diversa perche' i due
elementi sono caratterizzati in modo diverso e hanno tempistiche diverse
(vedi migrazione 0068_trincea_saggio_progettazione.sql).
Alla conferma NON crea ancora il rettangolo: restituisce solo i valori,
la creazione (click sulla mappa) la fa PosizionaRettangoloMapTool dopo che
il dialog si chiude.
"""

from qgis.PyQt.QtCore import QDate
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

METODO_TRINCEA = [("manuale", "Manuale"), ("meccanico", "Meccanico"), ("misto", "Misto")]
STATO_SAGGIO = [
    ("pianificata", "Pianificata"), ("in_corso", "In corso"),
    ("completata", "Completata"), ("sospesa", "Sospesa"),
]
ESITO_SAGGIO = [("da_verificare", "Da verificare"), ("negativo", "Negativo"), ("positivo", "Positivo")]


class RettangoloProgettazioneDialog(QDialog):
    def __init__(self, tipo, parent=None):
        """tipo: 'trincea' o 'saggio'."""
        super().__init__(parent)
        self.tipo = tipo
        self.setWindowTitle("Nuova trincea" if tipo == "trincea" else "Nuovo saggio")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Dopo «OK» clicca sulla mappa nel punto dove posizionare il rettangolo "
            "(centro); per ruotarlo usa poi «Ruota elemento» dallo stesso menu."
        ))
        form = QFormLayout()
        layout.addLayout(form)

        self.nomeInput = QLineEdit()
        prefisso = "TR-01, TR-02…" if tipo == "trincea" else "SAG-01, SAG-02…"
        self.nomeInput.setPlaceholderText(f"automatico ({prefisso})")
        form.addRow("Nome (opzionale)", self.nomeInput)

        self.larghezzaInput = QDoubleSpinBox()
        self.larghezzaInput.setRange(0.01, 999)
        self.larghezzaInput.setSuffix(" m")
        self.larghezzaInput.setValue(1.0)
        form.addRow("Larghezza", self.larghezzaInput)

        self.lunghezzaInput = QDoubleSpinBox()
        self.lunghezzaInput.setRange(0.01, 999)
        self.lunghezzaInput.setSuffix(" m")
        self.lunghezzaInput.setValue(5.0)
        form.addRow("Lunghezza", self.lunghezzaInput)

        self.profonditaInput = QDoubleSpinBox()
        self.profonditaInput.setRange(0, 999)
        self.profonditaInput.setSuffix(" m")
        form.addRow("Profondità (opzionale)", self.profonditaInput)

        if tipo == "trincea":
            self.metodoInput = QComboBox()
            for valore, etichetta in METODO_TRINCEA:
                self.metodoInput.addItem(etichetta, valore)
            form.addRow("Metodo", self.metodoInput)

            self.dataEsecuzioneInput = QDateEdit(QDate.currentDate())
            self.dataEsecuzioneInput.setCalendarPopup(True)
            form.addRow("Data esecuzione", self.dataEsecuzioneInput)
        else:
            self.statoInput = QComboBox()
            for valore, etichetta in STATO_SAGGIO:
                self.statoInput.addItem(etichetta, valore)
            form.addRow("Stato", self.statoInput)

            self.esitoInput = QComboBox()
            for valore, etichetta in ESITO_SAGGIO:
                self.esitoInput.addItem(etichetta, valore)
            form.addRow("Esito", self.esitoInput)

            self.dataAperturaInput = QDateEdit(QDate.currentDate())
            self.dataAperturaInput.setCalendarPopup(True)
            form.addRow("Data apertura", self.dataAperturaInput)

            self.dataChiusuraInput = QDateEdit(QDate.currentDate())
            self.dataChiusuraInput.setCalendarPopup(True)
            self.dataChiusuraInput.setEnabled(False)
            self.chiusuraCheck = QCheckBox("Imposta data di chiusura")
            self.chiusuraCheck.toggled.connect(self.dataChiusuraInput.setEnabled)
            form.addRow(self.chiusuraCheck, self.dataChiusuraInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per `attributi` di crea_feature_rettangolo, piu' larghezza/lunghezza a parte."""
        base = {
            "larghezza_m": self.larghezzaInput.value(),
            "lunghezza_m": self.lunghezzaInput.value(),
            "profondita_m": self.profonditaInput.value() or None,
            "angolo_rotazione_gradi": 0,
        }
        # Nome vuoto = non si scrive: lo assegna il database (TR-01 / SAG-01...,
        # trigger *_nome_auto, migrazione 0092), come per i carotaggi.
        nome = self.nomeInput.text().strip()
        if nome:
            base["nome"] = nome
        if self.tipo == "trincea":
            base["metodo"] = self.metodoInput.currentData()
            base["data_esecuzione"] = self.dataEsecuzioneInput.date().toString("yyyy-MM-dd")
        else:
            base["stato"] = self.statoInput.currentData()
            base["esito"] = self.esitoInput.currentData()
            base["data_apertura"] = self.dataAperturaInput.date().toString("yyyy-MM-dd")
            base["data_chiusura"] = (
                self.dataChiusuraInput.date().toString("yyyy-MM-dd") if self.chiusuraCheck.isChecked() else None
            )
        return base
