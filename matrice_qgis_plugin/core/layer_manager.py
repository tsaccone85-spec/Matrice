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

from qgis.core import QgsProject, QgsVectorLayer

# view/tabella -> colonna geometria, chiave primaria, nome visualizzato.
LAYER_SOURCES = {
    "rilievo_geometria": {
        "source": "v_rilievo_geometria_qgis",
        "geometry_column": "geom",
        "key_column": "id_geom",
        "display_name": "Rilievo vettoriale",
    },
    "rilievo_quota": {
        "source": "rilievo_quota",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Quote",
    },
    "carotaggio": {
        "source": "v_carotaggio_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Carotaggi",
    },
    "colonna_stratigrafica": {
        "source": "v_colonna_stratigrafica_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Colonne stratigrafiche",
    },
    "sezione_prospetto": {
        "source": "v_sezione_prospetto_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Sezioni e prospetti",
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
                opts["source"], geometry_column=opts["geometry_column"], key_column=opts["key_column"]
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
