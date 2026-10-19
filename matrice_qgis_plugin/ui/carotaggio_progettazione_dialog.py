"""
Dialog "Nuovo carotaggio" (menu Progettazione, matrice_plugin.py): raccoglie
i campi compilabili sul campo (descrizione/localizzazione/profondita/
diametro/metodo di perforazione). Stesso schema di RettangoloProgettazioneDialog
e RitrovamentoDialog: alla conferma NON crea ancora il punto, restituisce solo
i valori -- la creazione (click sulla mappa) la fa PosizionaPuntoMapTool dopo
che il dialog si chiude (core/ritrovamento_progettazione.py, riusato anche qui
perche' e' un tool generico "un click per posizionare un punto").

`numero_carotaggio` non e' un campo del dialog: lo calcola il trigger
INSTEAD OF INSERT di v_carotaggio_qgis (max+1 per quel cantiere), stesso
principio di numero_progressivo in RitrovamentoDialog. Gli altri campi della
scheda completa (esito, falda, stratigrafia, foto) restano da compilare in
Matrice (app web) o nella tabella attributi di QGIS dopo la creazione: qui
solo il minimo utile a registrare il punto mentre si e' sul campo.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class CarotaggioProgettazioneDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuovo carotaggio di progetto")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Carotaggio PREVISTO dalla progettazione (la lettura dei carotaggi "
            "eseguiti è un flusso di lavoro separato). Dopo «OK» clicca sulla "
            "mappa nel punto previsto: il codice (CAR-01, CAR-02…) è automatico."
        ))
        form = QFormLayout()
        layout.addLayout(form)

        self.descrizioneInput = QLineEdit()
        form.addRow("Descrizione (opzionale)", self.descrizioneInput)

        self.localizzazioneInput = QLineEdit()
        form.addRow("Localizzazione (opzionale)", self.localizzazioneInput)

        self.profonditaInput = QDoubleSpinBox()
        self.profonditaInput.setRange(0, 999)
        self.profonditaInput.setDecimals(2)
        self.profonditaInput.setSuffix(" m")
        form.addRow("Profondità (opzionale)", self.profonditaInput)

        self.diametroInput = QDoubleSpinBox()
        self.diametroInput.setRange(0, 999)
        self.diametroInput.setDecimals(1)
        self.diametroInput.setSuffix(" cm")
        form.addRow("Diametro (opzionale)", self.diametroInput)

        self.metodoInput = QLineEdit()
        self.metodoInput.setPlaceholderText("es. carotatrice meccanica, sonda manuale...")
        form.addRow("Metodo di perforazione (opzionale)", self.metodoInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per `attributi` di crea_feature_ritrovamento (vedi core/ritrovamento_progettazione.py)."""
        return {
            "descrizione": self.descrizioneInput.text().strip() or None,
            "localizzazione": self.localizzazioneInput.text().strip() or None,
            "profondita_m": self.profonditaInput.value() or None,
            "diametro_cm": self.diametroInput.value() or None,
            "metodo_perforazione": self.metodoInput.text().strip() or None,
        }
