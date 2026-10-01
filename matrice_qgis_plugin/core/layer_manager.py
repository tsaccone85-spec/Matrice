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
# il che fallisce sia a subset SQL vuoto (nessuna riga per il cantiere
# selezionato) sia -- caso di rilievo_geometria -- quando la colonna
# contiene geometrie di tipo diverso tra loro (poligoni per gli strati,
# linee per i tagli, punti per altre categorie: vedi config/styles/US.qml,
# che infatti categorizza con simboli fill/line/marker mescolati).
# Tutte le geometrie di Matrice sono in WGS84 (core/sync_handler.py,
# core/gps_import.py, core/dem_sampler.py usano tutti EPSG:4326).
LAYER_SOURCES = {
    "rilievo_geometria": {
        "source": "v_rilievo_geometria_qgis",
        "geometry_column": "geom",
        "key_column": "id_geom",
        "display_name": "Rilievo vettoriale",
        "wkb_type": Qgis.WkbType.Unknown,  # tipi misti: poligoni/linee/punti
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

    def load_cantiere(self, cantiere_id):
        """Carica (o ricarica) i layer del cantiere selezionato. Sezione 4, passo 1."""
        layers = {}
        for key, opts in LAYER_SOURCES.items():
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
            QgsProject.instance().addMapLayer(layer)
            layers[key] = layer

        self._layers_by_cantiere[cantiere_id] = layers
        return layers

    def current_layers(self, cantiere_id):
        return self._layers_by_cantiere.get(cantiere_id, {})

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
