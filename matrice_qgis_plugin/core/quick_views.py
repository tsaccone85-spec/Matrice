"""
Query prestabilite ("viste rapide"): Vista per fasi, Vedi solo le
sepolture, Vedi solo le USM (sezione 6, "Viste rapide predefinite").

Ogni vista applica insieme un filtro (subset string sui layer QGIS) e uno
stile, cosi' il risultato e' gia' pronto per il Print Layout senza passaggi
intermedi. Le viste sono definite in config/quick_views.json, non hardcoded,
cosi' da poterne aggiungere di nuove senza rilasciare una nuova versione
del plugin.

Gli stili .qml delle viste "Vedi solo le sepolture"/"Vedi solo le USM"
(forniti dall'utente, categorizzati sul campo `layer`) vivono in
config/styles/ -- vedi core/style_manager.py.
"""

import json
import os


class QuickViewManager:
    def __init__(self, config_path, style_manager, styles_dir=None):
        self.config_path = config_path
        self.style_manager = style_manager
        self.styles_dir = styles_dir
        self._views = self._load_config()

    def _load_config(self):
        with open(self.config_path, encoding="utf-8") as f:
            return json.load(f)["viste_rapide"]

    def available_views(self, layer_attivo=None):
        """
        Ritorna [(chiave, etichetta), ...] per popolare il menu a tendina del
        dock widget. Le viste con "richiede_layer_attivo" impostato (oggi: le
        3 viste sull'esito delle colonne stratigrafiche) compaiono solo
        quando `layer_attivo` corrisponde -- cioe' solo mentre l'operatore
        sta lavorando su quel layer in QGIS, per non proporre una vista che
        in quel momento non avrebbe alcun elemento da mostrare.
        """
        risultato = []
        for key, view in self._views.items():
            richiede = view.get("richiede_layer_attivo")
            if richiede and richiede != layer_attivo:
                continue
            risultato.append((key, view["etichetta"]))
        return risultato

    def apply(self, view_key, layers, fasi=None):
        """
        Applica la vista rapida `view_key` ai layer correnti del cantiere.
        `layers` = dizionario {"rilievo_geometria": QgsVectorLayer, "rilievo_quota": ..., ...}
        come restituito da LayerManager.current_layers() (chiavi di LAYER_SOURCES,
        core/layer_manager.py).

        `fasi`: lista dei nomi di fase distinti nel cantiere (da
        MatriceConnection.list_fasi), usata solo dalla vista "Vista per
        fasi" per assegnare colori casuali distinti (sezione 6); se non
        passata, i colori vengono assegnati sui valori letti direttamente
        dal layer (uniqueValues), con lo stesso risultato ma un giro in piu'
        sul dato gia' caricato.
        """
        view = self._views.get(view_key)
        if view is None:
            raise KeyError(f"Vista rapida sconosciuta: {view_key}")

        target = layers.get(view.get("layer", "rilievo_geometria"))
        if target is None:
            return

        # Filtro: subset string lato server (PostgreSQL), non filtro client.
        filtro = view.get("filtro_sql")
        target.setSubsetString(filtro or "")

        # Etichettatura alternativa (es. numero_tomba al posto del numero US).
        if view.get("campo_etichetta"):
            self._apply_label_field(target, view["campo_etichetta"])

        # Stile: .qml dedicato se presente, altrimenti simbologia categorica
        # sul campo indicato (es. fase, definizione, layer).
        if view.get("stile_qml"):
            known_fields = [f.name() for f in target.fields()]
            qml_path = self._resolve_qml_path(view["stile_qml"])
            self.style_manager.load_qml(target, qml_path, known_fields)
        elif view.get("campo_simbologia"):
            self._apply_categorized(target, view["campo_simbologia"], fasi, view.get("colori_fissi"))

        target.triggerRepaint()

    def _resolve_qml_path(self, stile_qml):
        if self.styles_dir and not os.path.isabs(stile_qml):
            return os.path.join(self.styles_dir, stile_qml)
        return stile_qml

    def _apply_label_field(self, layer, field_name):
        from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling

        settings = QgsPalLayerSettings()
        settings.fieldName = field_name
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)

    def _apply_categorized(self, layer, field_name, valori_noti=None, colori_fissi=None):
        """
        Simbologia categorica quando la vista rapida non ha uno stile .qml
        dedicato. Senza `colori_fissi` (oggi: "Vista per fasi", campo
        `fase`) il colore e' casuale ma sempre distinto per ogni valore
        (StyleManager.random_distinct_colors), cosi' due fasi non risultano
        mai dello stesso colore ne' facilmente confondibili. Con
        `colori_fissi` (oggi: le viste sull'esito delle colonne
        stratigrafiche, campo `esito`) il colore e' invece sempre lo stesso
        per ogni valore -- rosso/verde hanno un significato preciso
        (positivo/negativo), non vanno assegnati a caso.
        """
        from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
        from qgis.PyQt.QtGui import QColor

        if valori_noti is not None:
            valori = sorted(v for v in valori_noti if v not in (None, ""))
        else:
            field_index = layer.fields().indexOf(field_name)
            valori = sorted(v for v in layer.uniqueValues(field_index) if v not in (None, ""))

        colori_fissi = colori_fissi or {}
        da_assegnare = [v for v in valori if v not in colori_fissi]
        colori_random = iter(self.style_manager.random_distinct_colors(len(da_assegnare)))

        categories = []
        for valore in valori:
            if valore in colori_fissi:
                colore = QColor(colori_fissi[valore])
            else:
                colore = next(colori_random)
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(colore)
            categories.append(QgsRendererCategory(valore, symbol, str(valore)))

        layer.setRenderer(QgsCategorizedSymbolRenderer(field_name, categories))
