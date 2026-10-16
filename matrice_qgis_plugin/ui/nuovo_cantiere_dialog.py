"""
Dialog "Nuovo cantiere" (sezione 4, "Crea cantiere da QGIS"): raccoglie i
campi minimi richiesti da cantiere (nome, tipo intervento, comune,
provincia, regione, anno di esecuzione, email del referente di campo).
Alla conferma restituisce solo i valori -- la scrittura su Matrice (insert
su `cantiere` + righe `cantiere_accesso`) la fa matrice_plugin.py
(_handle_nuovo_cantiere), stesso principio degli altri dialog del plugin
(es. ui/ritrovamento_dialog.py).
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)
from qgis.PyQt.QtCore import QDate


class NuovoCantiereDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuovo cantiere")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.nomeCantiereInput = QLineEdit()
        form.addRow("Nome cantiere", self.nomeCantiereInput)

        self.tipoInterventoInput = QLineEdit()
        form.addRow("Tipo intervento", self.tipoInterventoInput)

        self.comuneInput = QLineEdit()
        form.addRow("Comune", self.comuneInput)

        self.provinciaInput = QLineEdit()
        form.addRow("Provincia", self.provinciaInput)

        self.regioneInput = QLineEdit()
        form.addRow("Regione", self.regioneInput)

        self.annoEsecuzioneInput = QSpinBox()
        self.annoEsecuzioneInput.setRange(1900, 2100)
        self.annoEsecuzioneInput.setValue(QDate.currentDate().year())
        form.addRow("Anno esecuzione", self.annoEsecuzioneInput)

        self.emailReferenteInput = QLineEdit()
        self.emailReferenteInput.setPlaceholderText("Lasciare vuoto per restare referente di campo")
        form.addRow("Email referente di campo (opzionale)", self.emailReferenteInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per matrice_plugin._handle_nuovo_cantiere."""
        return {
            "nome_cantiere": self.nomeCantiereInput.text().strip(),
            "tipo_intervento": self.tipoInterventoInput.text().strip() or None,
            "comune": self.comuneInput.text().strip() or None,
            "provincia": self.provinciaInput.text().strip() or None,
            "regione": self.regioneInput.text().strip() or None,
            "anno_esecuzione": self.annoEsecuzioneInput.value(),
            "email_referente": self.emailReferenteInput.text().strip(),
        }
