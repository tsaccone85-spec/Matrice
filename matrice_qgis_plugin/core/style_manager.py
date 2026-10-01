"""
Caricamento e validazione dei file di stile QGIS (.qml) per fasi e per US
(sezione 6 -- Iterazione 3 della roadmap).

Gli stili restano lato client: Matrice non li archivia mai.

Stili forniti dall'utente e inclusi in config/styles/ (categorizzati sul
campo `layer` di rilievo_geometria/v_rilievo_geometria_qgis -- da cui il
nome del campo, sezione 3/6):
- US.qml: stile di base applicato all'apertura tavola a tutto il layer
  (categorie per materiale/tipologia: Laterizio, Pietra, USM, Taglio, ...);
- Sepolture.qml: usato dalla vista rapida "Vedi solo le sepolture";
- USM.qml: usato dalla vista rapida "Vedi solo le USM".
NOTA: i file .qml originali categorizzano sull'attributo "Layer" (maiuscola);
rinominato in "layer" (come la colonna reale in produzione) al momento
dell'importazione in questo scheletro, altrimenti QGIS non trova il campo
(i nomi di campo sono case-sensitive).
"""

import random
import xml.etree.ElementTree as ET


class StyleValidationError(Exception):
    """Sollevata quando il .qml fa riferimento a un campo assente nello schema corrente."""

    def __init__(self, missing_field):
        self.missing_field = missing_field
        super().__init__(f"Lo stile fa riferimento al campo '{missing_field}' che non esiste in questo cantiere.")


class StyleManager:
    def load_qml(self, layer, qml_path, known_fields):
        """
        Applica un file .qml al layer, dopo aver verificato che i campi
        richiamati (es. fase, definizione, layer) esistano nello schema
        corrente. In caso contrario solleva StyleValidationError con un
        messaggio in linguaggio semplice, gestito dal dock widget (mai un
        errore tecnico grezzo di QGIS mostrato all'operatore).
        """
        referenced_fields = self._extract_referenced_fields(qml_path)
        missing = [f for f in referenced_fields if f not in known_fields]
        if missing:
            raise StyleValidationError(missing[0])

        message, ok = layer.loadNamedStyle(qml_path)
        if not ok:
            raise RuntimeError(message)
        layer.triggerRepaint()

    def apply_default_fase_style(self, layer, fasi):
        """
        Colore automatico per fase quando l'operatore non ha caricato un
        .qml personalizzato (sezione 6). `fase` e' un campo testo libero
        sulla scheda US, senza colore proprio salvato su Matrice: `fasi` e'
        la lista dei nomi di fase distinti presenti nel cantiere
        (es. da MatriceConnection.list_fasi), a cui viene assegnato un
        colore casuale ma sempre diverso da quello delle altre fasi
        (vedi random_distinct_colors).
        """
        from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol

        colori = self.random_distinct_colors(len(fasi))
        categories = []
        for nome_fase, colore in zip(fasi, colori):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(colore)
            categories.append(QgsRendererCategory(nome_fase, symbol, str(nome_fase)))

        renderer = QgsCategorizedSymbolRenderer("fase", categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    @staticmethod
    def random_distinct_colors(n):
        """
        Genera n colori casuali ma sempre ben distinti tra loro: punto di
        partenza sulla ruota HSV scelto a caso a ogni chiamata (quindi
        cambiano da un'apertura all'altra del plugin), poi distribuiti con
        l'angolo aureo (~137,5 gradi) cosi' che due fasi vicine in elenco
        non risultino mai simili nemmeno con molte fasi in un cantiere.
        Saturazione e luminosita' fisse per restare leggibili in stampa
        (tavole 1:50/1:100/1:200, sezione 7).
        """
        from qgis.PyQt.QtGui import QColor

        if n <= 0:
            return []

        golden_ratio_conjugate = 0.6180339887498949
        hue = random.random()
        colors = []
        for _ in range(n):
            hue = (hue + golden_ratio_conjugate) % 1.0
            colors.append(QColor.fromHsvF(hue, 0.55, 0.85))
        return colors

    def _extract_referenced_fields(self, qml_path):
        tree = ET.parse(qml_path)
        fields = set()
        for el in tree.iter():
            # I campi lato singolo simbolo/etichetta usano l'attributo "field";
            # il renderer categorizzato principale usa invece "attr"
            # (es. <renderer-v2 ... attr="layer" type="categorizedSymbol">).
            if el.get("field"):
                fields.add(el.get("field"))
            if el.tag == "renderer-v2" and el.get("attr"):
                fields.add(el.get("attr"))
        return sorted(fields)
