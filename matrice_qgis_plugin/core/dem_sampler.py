"""
Campionamento della quota s.l.m. da un DEM (modello digitale del terreno),
usato quando una geometria puntuale (carotaggio, colonna stratigrafica,
sezione/prospetto) viene spostata in QGIS e l'operatore NON ha impostato
manualmente la quota: la quota va ricalcolata dal DEM, non lasciata quella
del punto precedente.

Il DEM e' referenziato per cantiere in `cantiere.dem_url` (path o URL di un
raster leggibile da QGIS, es. un file locale condiviso o un COG). Se il
cantiere non ha un DEM configurato, il campionamento e' saltato e il plugin
lo segnala in linguaggio semplice (mai un errore tecnico grezzo).
"""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
)


class DemNotAvailableError(Exception):
    """Nessun DEM configurato per il cantiere, o raster non caricabile."""


class DemSampler:
    def __init__(self):
        self._raster_cache = {}  # dem_url -> QgsRasterLayer

    def _load_dem(self, dem_url):
        if not dem_url:
            raise DemNotAvailableError(
                "Nessun DEM impostato per questo cantiere: la quota non è stata ricalcolata."
            )
        if dem_url not in self._raster_cache:
            layer = QgsRasterLayer(dem_url, "DEM", "gdal")
            if not layer.isValid():
                raise DemNotAvailableError(
                    f"Il DEM configurato per il cantiere non è leggibile ({dem_url})."
                )
            self._raster_cache[dem_url] = layer
        return self._raster_cache[dem_url]

    def sample_elevation(self, dem_url, lat, lng, source_crs="EPSG:4326"):
        """
        Ritorna la quota (nelle unita' verticali del DEM, tipicamente metri
        s.l.m.) nel punto (lat, lng). Solleva DemNotAvailableError se il DEM
        non e' configurato/leggibile o il punto cade fuori dalla sua estensione.
        """
        dem_layer = self._load_dem(dem_url)

        point = QgsPointXY(lng, lat)
        src_crs = QgsCoordinateReferenceSystem(source_crs)
        dem_crs = dem_layer.crs()
        if src_crs != dem_crs:
            transform = QgsCoordinateTransform(src_crs, dem_crs, QgsProject.instance())
            point = transform.transform(point)

        value, ok = dem_layer.dataProvider().sample(point, 1)
        if not ok:
            raise DemNotAvailableError(
                "Il punto spostato cade fuori dall'area coperta dal DEM: quota non ricalcolata."
            )
        return value
