"""
Dialog "Crea buffer area di indagine" (menu Progettazione,
matrice_plugin.py): chiede solo la distanza in metri -- non precompilata
sul cantiere, richiesta esplicitamente ad ogni generazione (il buffer si
rigenera da capo ogni volta, non e' un parametro salvato).
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
)


class BufferAreaDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crea buffer area di indagine")

        form = QFormLayout(self)

        self.distanzaInput = QDoubleSpinBox()
        self.distanzaInput.setRange(1, 5000)
        self.distanzaInput.setSuffix(" m")
        self.distanzaInput.setValue(100)
        form.addRow("Distanza", self.distanzaInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        form.addRow(pulsanti)

    def valori(self):
        return {"distanza_m": self.distanzaInput.value()}
