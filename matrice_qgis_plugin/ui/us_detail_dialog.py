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
statico sarebbe enorme e fragile ad ogni modifica dello schema reale.

Nessuna dipendenza esterna per le immagini (stessa scelta di
core/supabase_client.py): urllib + QPixmap.loadFromData, non requests.
"""

import urllib.error
import urllib.request

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

IMAGE_TIMEOUT_SECONDS = 10
IMAGE_PREVIEW_WIDTH = 320

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

# Campi testuali lunghi: QPlainTextEdit invece di una riga sola, per
# restare leggibili. Riconosciuti per sottostringa nel nome campo, non con
# un elenco esaustivo (nomi nuovi non riconosciuti restano comunque
# editabili, solo su una riga sola).
INDIZI_TESTO_LUNGO = (
    "descrizione", "osservazion", "interpretazione", "note", "criteri",
    "elementi_datanti", "consolidanti", "campionature", "documentazione",
    "parti_scheletro", "alterazioni", "corredo", "riferimenti_stratigrafici",
    "contenitori", "collocazione", "misure", "tecniche_recupero",
    "connessioni_scheletriche", "natura_rimaneggiamento", "reperti_associati",
)


def _etichetta_leggibile(nome_campo):
    return nome_campo.replace("_", " ").capitalize()


def _e_testo_lungo(nome_campo):
    return any(indizio in nome_campo for indizio in INDIZI_TESTO_LUNGO)


def _carica_pixmap(url):
    """Scarica un'immagine da URL e la ritorna come QPixmap, o None se non disponibile (mai un'eccezione)."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=IMAGE_TIMEOUT_SECONDS) as response:
            dati = response.read()
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(dati):
        return None
    if pixmap.width() > IMAGE_PREVIEW_WIDTH:
        pixmap = pixmap.scaledToWidth(IMAGE_PREVIEW_WIDTH, Qt.SmoothTransformation)
    return pixmap


def _etichetta_foto(url, didascalia=None):
    """QLabel con l'anteprima immagine (o un avviso testuale se non disponibile)."""
    label = QLabel()
    pixmap = _carica_pixmap(url)
    if pixmap is not None:
        label.setPixmap(pixmap)
    else:
        label.setText("(immagine non disponibile)" if url else "(nessuna foto)")
        label.setStyleSheet("color: #666;")
    if didascalia:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        caption = QLabel(didascalia)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        return wrapper
    return label


class DynamicRecordForm(QWidget):
    """
    Form generato dai campi di una riga (dict) restituita da PostgREST,
    escludendo le colonne tecniche indicate. Un'istanza per record (US,
    un singolo reperto, o la sepoltura).
    """

    def __init__(self, dati, campi_esclusi, parent=None):
        super().__init__(parent)
        self._tipi_originali = {}
        self._widgets = {}

        layout = QFormLayout(self)
        for nome_campo, valore in dati.items():
            if nome_campo in campi_esclusi:
                continue
            self._tipi_originali[nome_campo] = type(valore)
            widget = self._crea_widget(nome_campo, valore)
            self._widgets[nome_campo] = widget
            layout.addRow(_etichetta_leggibile(nome_campo), widget)

    def _crea_widget(self, nome_campo, valore):
        if isinstance(valore, bool):
            widget = QCheckBox()
            widget.setChecked(valore)
            return widget
        if isinstance(valore, list):
            widget = QLineEdit(", ".join(str(v) for v in valore))
            return widget
        testo = "" if valore is None else str(valore)
        if _e_testo_lungo(nome_campo):
            widget = QPlainTextEdit(testo)
            widget.setFixedHeight(60)
            return widget
        return QLineEdit(testo)

    def valori_correnti(self):
        """Ritorna {nome_campo: valore} pronto per un PATCH, riconvertito al tipo originale."""
        risultato = {}
        for nome_campo, widget in self._widgets.items():
            tipo_originale = self._tipi_originali[nome_campo]
            if isinstance(widget, QCheckBox):
                risultato[nome_campo] = widget.isChecked()
            elif isinstance(widget, QPlainTextEdit):
                risultato[nome_campo] = widget.toPlainText() or None
            elif tipo_originale is list:
                testo = widget.text().strip()
                risultato[nome_campo] = [v.strip() for v in testo.split(",") if v.strip()] if testo else []
            elif tipo_originale in (int, float):
                testo = widget.text().strip()
                try:
                    risultato[nome_campo] = tipo_originale(testo) if testo else None
                except ValueError:
                    risultato[nome_campo] = testo  # lasciato cosi': l'API segnalera' l'errore in modo chiaro
            else:
                risultato[nome_campo] = widget.text() or None
        return risultato


def _in_area_scorrevole(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class UsDetailDialog(QDialog):
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
    def _token(self):
        return self.session.access_token

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
        self.tabs.addTab(_in_area_scorrevole(self._us_form), "US")

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
        foto_layout.addWidget(_etichetta_foto(reperto.get("foto_1_url"), "Foto 1"))
        foto_layout.addWidget(_etichetta_foto(reperto.get("foto_2_url"), "Foto 2"))
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
            layout.addWidget(_etichetta_foto(foto.get("file_url"), didascalia or foto.get("nome_file")))
        layout.addStretch()
        return _in_area_scorrevole(contenitore)

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
        return _in_area_scorrevole(self._sepoltura_form)

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
