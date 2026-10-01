"""
Pezzi condivisi dalle finestre di dettaglio/editing di un record Matrice
(scheda US in us_detail_dialog.py, scheda colonna stratigrafica/carotaggio
in punto_dettaglio_dialog.py): il form generato dinamicamente dai campi
restituiti da PostgREST, e il caricamento delle foto dal bucket Storage
privato (vedi nota su matrice_api.scarica_foto).
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

IMAGE_PREVIEW_WIDTH = 320

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


class DynamicRecordForm(QWidget):
    """
    Form generato dai campi di una riga (dict) restituita da PostgREST,
    escludendo le colonne tecniche indicate. Un'istanza per record (US, un
    singolo reperto, la sepoltura, una colonna stratigrafica, un carotaggio...).
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


def in_area_scorrevole(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class SchedaConFotoBase:
    """
    Mixin per le finestre di dettaglio che mostrano foto dal bucket privato
    (vedi nota su matrice_api.scarica_foto): la classe che la usa deve
    avere self.matrice_api e self.session gia' impostati.
    """

    def _token(self):
        return self.session.access_token

    def _carica_pixmap(self, percorso_relativo):
        """Scarica una foto dal bucket privato e la ritorna come QPixmap, o None (mai un'eccezione)."""
        if not percorso_relativo:
            return None
        dati = self.matrice_api.scarica_foto(percorso_relativo, self._token())
        if dati is None:
            return None
        pixmap = QPixmap()
        if not pixmap.loadFromData(dati):
            return None
        if pixmap.width() > IMAGE_PREVIEW_WIDTH:
            pixmap = pixmap.scaledToWidth(IMAGE_PREVIEW_WIDTH, Qt.SmoothTransformation)
        return pixmap

    def _etichetta_foto(self, percorso_relativo, didascalia=None):
        """QLabel con l'anteprima immagine (o un avviso testuale se non disponibile)."""
        label = QLabel()
        pixmap = self._carica_pixmap(percorso_relativo)
        if pixmap is not None:
            label.setPixmap(pixmap)
        else:
            label.setText("(immagine non disponibile)" if percorso_relativo else "(nessuna foto)")
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
