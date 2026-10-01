"""
Caricamento dei layer da Matrice come layer PostGIS nativi di QGIS,
filtrati sul cantiere corrente (sezione 4, passo 1).

Nessun file temporaneo: QgsVectorLayer punta direttamente al database
(tabelle o viste), quindi ogni sessione di editing legge e scrive
direttamente su Matrice.

Le geometrie vere e proprie (US) passano dalla vista v_rilievo_geometria_qgis
(gia' unita a fase/definizione/sepoltura, sezione 3). Carotaggi, colonne
stratigrafiche e sezioni/prospetto -- tabelle con lat/lng dirette, non
geometria PostGIS -- passano dalle rispettive viste aggiornabili, che
espongono un campo `geom` calcolato e traducono le modifiche di posizione
in scritture su lat/lng tramite trigger lato database.
"""

from qgis.core import Qgis, QgsProject, QgsVectorLayer

# view/tabella -> colonna geometria, chiave primaria, nome visualizzato,
# tipo WKB e SRID (sezione 4, passo 1).
#
# wkb_type/srid sono dichiarati qui esplicitamente e passati a
# db_connector.postgis_uri(): senza, QGIS deve dedurli campionando i dati,
# il che fallisce a subset SQL vuoto (nessuna riga per il cantiere
# selezionato, o tabella non ancora popolata) -- il provider non ha nulla
# da campionare e segna il layer non valido.
# Tipo confermato direttamente dal catalogo PostGIS (geometry_columns:
# "punto", srid 4326) il 2026-10-01 -- NON e' geometria mista nonostante
# config/styles/US.qml contenga anche simboli fill/line: quei livelli sono
# probabilmente simboli annidati/non attivi per questo layer, non categorie
# rilevanti per rilievo_geometria.
# Tutte le geometrie di Matrice sono in WGS84 (core/sync_handler.py,
# core/gps_import.py, core/dem_sampler.py usano tutti EPSG:4326).
LAYER_SOURCES = {
    "rilievo_geometria": {
        "source": "v_rilievo_geometria_qgis",
        "geometry_column": "geom",
        "key_column": "id_geom",
        "display_name": "Rilievo vettoriale",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
    },
    "rilievo_quota": {
        "source": "rilievo_quota",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Quote",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
    },
    "carotaggio": {
        "source": "v_carotaggio_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Carotaggi",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
    },
    "colonna_stratigrafica": {
        "source": "v_colonna_stratigrafica_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Colonne stratigrafiche",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
    },
    "sezione_prospetto": {
        "source": "v_sezione_prospetto_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Sezioni e prospetti",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
    },
}


class LayerManager:
    def __init__(self, connection):
        self.connection = connection
        self._layers_by_cantiere = {}

    def _build_layer(self, cantiere_id, key):
        """Costruisce (senza aggiungere in mappa) il layer PostGIS per `key`."""
        opts = LAYER_SOURCES[key]
        uri = self.connection.postgis_uri(
            opts["source"],
            geometry_column=opts["geometry_column"],
            key_column=opts["key_column"],
            wkb_type=opts["wkb_type"],
            srid=opts["srid"],
        )
        # cantiere_id e' un uuid, non un intero: va quotato nel subset SQL.
        uri.setSql(f"cantiere_id = '{cantiere_id}'")
        layer = QgsVectorLayer(uri.uri(False), f"{opts['display_name']} - cantiere {cantiere_id}", "postgres")
        if not layer.isValid():
            raise RuntimeError(f"Impossibile caricare il layer '{key}' da Matrice.")
        return layer

    def load_cantiere(self, cantiere_id):
        """
        Carica (o ricarica) i layer del cantiere selezionato (sezione 4,
        passo 1). Per non affollare la mappa di livelli vuoti al primo
        utilizzo (es. un cantiere appena iniziato, dove magari e' stato
        rilevato solo un tipo di dato), vengono aggiunti subito solo i
        livelli che hanno gia' almeno una riga per questo cantiere; gli
        altri restano disponibili a richiesta (vedi show_layer), spuntabili
        dal pannello Matrice.

        Ritorna (layers, has_data): entrambi dict con le stesse chiavi di
        LAYER_SOURCES; layers contiene sempre l'oggetto QgsVectorLayer
        (anche per quelli non ancora aggiunti in mappa), has_data dice se
        all'apertura aveva gia' righe (quindi e' stato aggiunto subito).
        """
        layers = {}
        has_data = {}
        for key in LAYER_SOURCES:
            layer = self._build_layer(cantiere_id, key)
            layers[key] = layer
            if layer.featureCount() > 0:
                QgsProject.instance().addMapLayer(layer)
                has_data[key] = True
            else:
                has_data[key] = False

        self._layers_by_cantiere[cantiere_id] = layers
        return layers, has_data

    def current_layers(self, cantiere_id):
        return self._layers_by_cantiere.get(cantiere_id, {})

    def is_layer_shown(self, cantiere_id, key):
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        return layer is not None and QgsProject.instance().mapLayer(layer.id()) is not None

    def show_layer(self, cantiere_id, key):
        """
        Aggiunge in mappa il livello `key` del cantiere, su richiesta
        dell'operatore (spunta nel pannello). Se era gia' stato rimosso in
        precedenza lo ricostruisce da capo (l'oggetto QGIS viene distrutto
        da removeMapLayer). Ritorna (layer, era_gia_visibile): il secondo
        valore serve a chi chiama per capire se e' la prima volta che
        questo layer compare in mappa per questo cantiere (e quindi va
        agganciato alla sincronizzazione delle modifiche).
        """
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        if layer is not None and QgsProject.instance().mapLayer(layer.id()) is not None:
            return layer, True  # gia' visibile, nessuna nuova azione da fare

        layer = self._build_layer(cantiere_id, key)
        QgsProject.instance().addMapLayer(layer)
        self._layers_by_cantiere.setdefault(cantiere_id, {})[key] = layer
        return layer, False

    def hide_layer(self, cantiere_id, key):
        """Rimuove dalla mappa il livello `key` del cantiere (spunta tolta)."""
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        if layer is None:
            return
        if QgsProject.instance().mapLayer(layer.id()) is not None:
            QgsProject.instance().removeMapLayer(layer.id())
        self._layers_by_cantiere.get(cantiere_id, {}).pop(key, None)

    # Le tre viste puntuali con lat/lng dirette, sincronizzate tramite
    # point_position_sync.py (distinte dalla geometria US, che usa il
    # percorso generico sync_handler.py). Il ricalcolo della quota dal DEM
    # riguarda pero' solo carotaggio e colonna_stratigrafica, non
    # sezione_prospetto -- vedi PointPositionSyncHandler.DEM_TABLES.
    POINT_SYNC_TABLES = {
        "carotaggio": "carotaggio",
        "colonna_stratigrafica": "colonna_stratigrafica",
        "sezione_prospetto": "sezione_prospetto",
    }
