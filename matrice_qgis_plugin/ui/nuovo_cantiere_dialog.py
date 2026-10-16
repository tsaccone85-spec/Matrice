"""
Dialog "Nuovo cantiere" (sezione 4, "Crea cantiere da QGIS",
matrice_plugin.py `_handle_nuovo_cantiere`): raccoglie i dati minimi per
inserire una riga su `cantiere` -- nome, tipo di lavoro, localizzazione di
massima, anno, email del referente di campo.

Il tipo di lavoro (`tipo_intervento`) e' una QComboBox e non una lista
fissa uguale per tutti: mostra solo i tipi che l'utente loggato puo'
effettivamente usare (core/matrice_api.py `tipi_intervento_consentiti`),
calcolati PRIMA di apire il dialog e passati al costruttore -- stesso
principio di `crea_cantiere`, la decisione sui permessi resta a
Matrice/Supabase (funzione_accesso_utente), il dialog si limita a non
mostrare scelte che verrebbero comunque rifiutate dalla RLS lato server.
"""

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class NuovoCantiereDialog(QDialog):
    def __init__(self, tipi_intervento_consentiti, parent=None):
        """
        tipi_intervento_consentiti: lista di tuple (valore, etichetta) --
        vedi core/matrice_api.py `TIPI_INTERVENTO` e
        `tipi_intervento_consentiti()` -- gia' filtrata per il profilo
        dell'utente loggato. Non puo' essere vuota: se lo fosse, il
        chiamante non dovrebbe arrivare ad aprire questo dialog.
        """
        super().__init__(parent)
        self.setWindowTitle("Nuovo cantiere")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Crea un nuovo cantiere su Matrice. Per i campi di dettaglio "
            "(indirizzo, responsabile scientifico, ecc.) si usa poi la "
            "scheda cantiere sull'app web."
        ))
        form = QFormLayout()
        layout.addLayout(form)

        self.nomeInput = QLineEdit()
        form.addRow("Nome cantiere", self.nomeInput)

        self.tipoInterventoInput = QComboBox()
        for valore, etichetta in tipi_intervento_consentiti:
            self.tipoInterventoInput.addItem(etichetta, valore)
        form.addRow("Tipo di lavoro", self.tipoInterventoInput)

        self.comuneInput = QLineEdit()
        form.addRow("Comune", self.comuneInput)

        self.provinciaInput = QLineEdit()
        form.addRow("Provincia", self.provinciaInput)

        self.regioneInput = QLineEdit()
        form.addRow("Regione", self.regioneInput)

        self.annoEsecuzioneInput = QSpinBox()
        self.annoEsecuzioneInput.setRange(1900, 2100)
        self.annoEsecuzioneInput.setValue(_anno_corrente())
        form.addRow("Anno esecuzione", self.annoEsecuzioneInput)

        self.emailReferenteInput = QLineEdit()
        self.emailReferenteInput.setPlaceholderText("Lasciare vuoto per impostare l'utente connesso")
        form.addRow("Email referente di campo", self.emailReferenteInput)

        pulsanti = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        pulsanti.accepted.connect(self.accept)
        pulsanti.rejected.connect(self.reject)
        layout.addWidget(pulsanti)

    def valori(self):
        """Dict pronto per `campi` di matrice_api.crea_cantiere, piu' email_referente a parte."""
        return {
            "nome_cantiere": self.nomeInput.text().strip() or "Nuovo cantiere",
            "tipo_intervento": self.tipoInterventoInput.currentData(),
            "comune": self.comuneInput.text().strip() or None,
            "provincia": self.provinciaInput.text().strip() or None,
            "regione": self.regioneInput.text().strip() or None,
            "anno_esecuzione": self.annoEsecuzioneInput.value(),
            "email_referente": self.emailReferenteInput.text().strip(),
        }


def _anno_corrente():
    from datetime import date
    return date.today().year
