"""
Scheda US completa: dettaglio/editing di una singola unita' stratigrafica,
dei reperti collegati (con anteprima foto) e della sepoltura collegata se
presente, piu' la galleria delle foto di scavo.

Si apre con un doppio click su un elemento gia' selezionato del layer
"Rilievo vettoriale" (vedi matrice_plugin.py). Aggiorna "il modo di vedere
i dati" previsto nella prima versione del plugin (principio "elenco":
vedi nota storica in models/data_model.py) -- qui serve davvero la scheda
intera, non solo l'elenco leggero usato per l'associazione.

Il form e' generato dinamicamente dai campi restituiti da PostgREST
(core/matrice_api.py), non disegnato a mano campo per campo: US e
sepoltura hanno rispettivamente ~40 e ~50 campi testuali/numerici, un .ui
statico sarebbe enorme e fragile ad ogni modifica dello schema reale
(vedi ui/scheda_comune.py, condiviso anche con
ui/punto_dettaglio_dialog.py per colonna stratigrafica/carotaggio).

Le foto (foto_1_url/foto_2_url su reperto, file_url su foto_scavo) NON
sono URL diretti: sono percorsi relativi dentro un bucket Storage privato
(verificato su Supabase), quindi vanno scaricate con il token dell'utente
tramite matrice_api.scarica_foto(), non con un link aperto a caso -- un
bucket privato rifiuterebbe comunque una richiesta anonima.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .scheda_comune import DynamicRecordForm, SchedaConFotoBase, in_area_scorrevole

# Campi tecnici/di controllo da non mostrare in un form editabile (chiavi,
# riferimenti ad altre tabelle, colonne di audit): per tutto il resto il
# form si genera dai nomi di colonna cosi' come arrivano da PostgREST.
US_CAMPI_ESCLUSI = {
    "id", "cantiere_id", "created_by", "created_at", "updated_at",
    "origine_ocr_id", "responsabile_scientifico_id", "responsabile_compilazione_utente_id",
}
SEPOLTURA_CAMPI_ESCLUSI = {"id", "cantiere_id", "created_by", "created_at", "updated_at", "origine_ocr_id"}
REPERTO_CAMPI_ESCLUSI = {
    "id", "cantiere_id", "us_id", "origine_ocr_id", "created_at", "foto_1_url", "foto_2_url",
}


class UsDetailDialog(QDialog, SchedaConFotoBase):
    """
    Scheda US (sezione "principio elenco" superata su richiesta esplicita
    dell'operatore): tab US, Reperti (con foto), Foto di scavo, Sepoltura.
    """

    def __init__(self, matrice_api, session, us_id, parent=None):
        super().__init__(parent)
        self.matrice_api = matrice_api
        self.session = session
        self.us_id = us_id
        self._reperti = []
        self._reperto_form_corrente = None
        self._sepoltura_form = None
        self._sepoltura_id = None

        self.setWindowTitle("Scheda US")
        self.resize(640, 640)

        layout = QVBoxLayout(self)
        self.errorLabel = QLabel("")
        self.errorLabel.setStyleSheet("color: #b3261e;")
        self.errorLabel.setWordWrap(True)
        layout.addWidget(self.errorLabel)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        pulsanti = QHBoxLayout()
        pulsanti.addStretch()
        self.saveButton = QPushButton("Salva modifiche")
        self.saveButton.clicked.connect(self._salva)
        pulsanti.addWidget(self.saveButton)
        closeButton = QPushButton("Chiudi")
        closeButton.clicked.connect(self.accept)
        pulsanti.addWidget(closeButton)
        layout.addLayout(pulsanti)

        self._carica_dati()

    # ------------------------------------------------------------------
    def _carica_dati(self):
        try:
            us_row = self.matrice_api.get_us(self.us_id, self._token())
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            return
        if us_row is None:
            self.errorLabel.setText("US non trovata (o non piu' accessibile).")
            return

        self.setWindowTitle(f"Scheda US {us_row.get('numero_us', '')}")
        self._us_form = DynamicRecordForm(us_row, US_CAMPI_ESCLUSI)
        self.tabs.addTab(in_area_scorrevole(self._us_form), "US")

        self.tabs.addTab(self._costruisci_tab_reperti(), "Reperti")
        self.tabs.addTab(self._costruisci_tab_foto(), "Foto di scavo")

        self._sepoltura_id = self._trova_sepoltura_id(us_row)
        if self._sepoltura_id:
            self.tabs.addTab(self._costruisci_tab_sepoltura(), "Sepoltura")

    def _trova_sepoltura_id(self, us_row):
        """`us` non ha una FK diretta a sepoltura: il legame passa da sepoltura_us (vedi README)."""
        try:
            rows = self.matrice_api.rest.select(
                "sepoltura_us", self._token(), params={"us_id": f"eq.{self.us_id}", "select": "sepoltura_id"}
            )
        except Exception:
            return None
        return rows[0]["sepoltura_id"] if rows else None

    # ------------------------------------------------------------------
    # Tab Reperti: elenco a sinistra, form del reperto selezionato a destra
    # ------------------------------------------------------------------
    def _costruisci_tab_reperti(self):
        contenitore = QWidget()
        layout = QHBoxLayout(contenitore)

        try:
            self._reperti = self.matrice_api.list_reperti(self.us_id, self._token())
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            self._reperti = []

        self.reperti_list = QListWidget()
        for reperto in self._reperti:
            testo = f"N. {reperto.get('numero_reperto', '?')} - {reperto.get('oggetto') or ''}".rstrip(" -")
            item = QListWidgetItem(testo)
            item.setData(Qt.UserRole, reperto["id"])
            self.reperti_list.addItem(item)
        self.reperti_list.currentRowChanged.connect(self._mostra_reperto)
        self.reperti_list.setMaximumWidth(220)
        layout.addWidget(self.reperti_list)

        self.reperto_dettaglio_area = QScrollArea()
        self.reperto_dettaglio_area.setWidgetResizable(True)
        layout.addWidget(self.reperto_dettaglio_area, stretch=1)

        if self._reperti:
            self.reperti_list.setCurrentRow(0)
        return contenitore

    def _mostra_reperto(self, indice_riga):
        self._reperto_form_corrente = None
        if indice_riga < 0 or indice_riga >= len(self._reperti):
            self.reperto_dettaglio_area.setWidget(QWidget())
            return

        reperto = self._reperti[indice_riga]
        contenitore = QWidget()
        layout = QVBoxLayout(contenitore)

        foto_layout = QHBoxLayout()
        foto_layout.addWidget(self._etichetta_foto(reperto.get("foto_1_url"), "Foto 1"))
        foto_layout.addWidget(self._etichetta_foto(reperto.get("foto_2_url"), "Foto 2"))
        layout.addLayout(foto_layout)

        self._reperto_form_corrente = DynamicRecordForm(reperto, REPERTO_CAMPI_ESCLUSI)
        self._reperto_form_corrente.reperto_id = reperto["id"]
        layout.addWidget(self._reperto_form_corrente)

        self.reperto_dettaglio_area.setWidget(contenitore)

    # ------------------------------------------------------------------
    # Tab Foto di scavo: sola lettura, galleria con didascalia
    # ------------------------------------------------------------------
    def _costruisci_tab_foto(self):
        try:
            foto_rows = self.matrice_api.list_foto_scavo(self.us_id, self._token())
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            foto_rows = []

        contenitore = QWidget()
        layout = QVBoxLayout(contenitore)
        if not foto_rows:
            layout.addWidget(QLabel("Nessuna foto di scavo collegata a questa US."))
        for foto in foto_rows:
            pezzi_didascalia = [
                f"#{foto.get('numero_progressivo')}" if foto.get("numero_progressivo") else None,
                foto.get("lavagna_testo"),
                foto.get("data_scatto"),
            ]
            didascalia = " - ".join(p for p in pezzi_didascalia if p)
            layout.addWidget(self._etichetta_foto(foto.get("file_url"), didascalia or foto.get("nome_file")))
        layout.addStretch()
        return in_area_scorrevole(contenitore)

    # ------------------------------------------------------------------
    # Tab Sepoltura
    # ------------------------------------------------------------------
    def _costruisci_tab_sepoltura(self):
        try:
            sepoltura_row = self.matrice_api.get_sepoltura(self._sepoltura_id, self._token())
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            sepoltura_row = None
        if sepoltura_row is None:
            return QLabel("Sepoltura non trovata (o non piu' accessibile).")
        self._sepoltura_form = DynamicRecordForm(sepoltura_row, SEPOLTURA_CAMPI_ESCLUSI)
        return in_area_scorrevole(self._sepoltura_form)

    # ------------------------------------------------------------------
    def _salva(self):
        self.errorLabel.setText("")
        try:
            self.matrice_api.update_us(self.us_id, self._us_form.valori_correnti(), self._token())
            if self._reperto_form_corrente is not None:
                self.matrice_api.update_reperto(
                    self._reperto_form_corrente.reperto_id,
                    self._reperto_form_corrente.valori_correnti(),
                    self._token(),
                )
            if self._sepoltura_form is not None and self._sepoltura_id:
                self.matrice_api.update_sepoltura(
                    self._sepoltura_id, self._sepoltura_form.valori_correnti(), self._token()
                )
        except Exception as exc:
            self.errorLabel.setText(str(exc))
            return
        QMessageBox.information(self, "Matrice", "Modifiche salvate.")
