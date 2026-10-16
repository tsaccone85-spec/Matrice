#!/usr/bin/env python3
"""Download automatico di ortofoto (Sentinel-2) e DTM (Copernicus DEM) senza
alcuna interazione manuale, per il flusso --auto di rileva_segni.py.

ATTENZIONE SUI LIMITI DI RISOLUZIONE
-------------------------------------
Sentinel-2 ha risoluzione 10 m/pixel sulle bande usate per NDVI (B04 rosso,
B08 NIR). Su un buffer di poche centinaia di metri (il caso d'uso tipico di
questo strumento) significa poche decine di pixel per lato: un fossato o un
muro sepolto largo 1-2 m NON è risolvibile, e sparisce dentro il rumore di
un singolo pixel insieme al terreno circostante. Questa modalità è pensata
per chi preferisce una pipeline a zero interazione manuale accettando questo
limite; per un'analisi affidabile su piccola scala resta preferibile la
modalità manuale con ortofoto/DTM ad alta risoluzione scaricati dal
geoportale regionale (vedi README, sezione "Modalità alta risoluzione").

Il DTM (Copernicus DEM GLO-30, 30 m/pixel) è ancora più grossolano: utile
solo come controllo di contesto, non per il microrilievo fine.

CREDENZIALI
-----------
Sentinel-2 richiede una Client ID / Client Secret gratuiti del Copernicus
Data Space Ecosystem (registrazione libera una tantum su
https://dataspace.copernicus.eu -> "Account Settings" -> "OAuth clients").
Una volta ottenute, lo script non chiede più nulla all'utente: le credenziali
si passano come variabili d'ambiente COPERNICUS_CLIENT_ID /
COPERNICUS_CLIENT_SECRET (o parametri CLI) e da quel momento il download è
interamente automatico, per qualunque cantiere e qualunque data.

Il Copernicus DEM non richiede invece alcuna credenziale: è ospitato in
accesso pubblico anonimo su un bucket AWS Open Data.

NOTA IMPORTANTE: questo modulo è stato scritto seguendo la documentazione
pubblica delle API (Copernicus Data Space Ecosystem Process API v1,
Copernicus DEM su AWS Open Data) ma non è stato possibile testarlo contro i
servizi reali nell'ambiente in cui è stato sviluppato, perché l'accesso di
rete era limitato a un elenco ristretto di host (niente Copernicus). Prima
di un uso in produzione, verificare con un piccolo test (`--auto` su un'area
minuscola) che il formato di risposta corrisponda a quanto atteso; le API
Copernicus possono anche cambiare nel tempo.
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.merge import merge as rio_merge
from rasterio.warp import transform_bounds

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Il modulo 'requests' è necessario per il download automatico: "
        "pip install requests"
    ) from exc


CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
CDSE_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Bucket pubblico, accesso anonimo (nessuna credenziale richiesta).
COPERNICUS_DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"

RISOLUZIONE_SENTINEL2_M = 10.0

# Numero massimo di acquisizioni stagionali consentite per un'analisi
# multi-temporale. Definito qui come unica sorgente di verità: sia
# server_locale.py sia rileva_segni.py importano questa costante invece
# di ridefinirla localmente (lacuna 11: due copie diverganti).
MASSIMO_STAGIONI = 5

# Metri per grado di latitudine: approssimazione sferica, costante a tutte
# le latitudini (l'ellissoide introduce un errore < 1% tra equatore e poli).
_M_PER_GRADO_LAT = 111_000.0


def metri_per_grado_lon(lat_centrale: float) -> float:
    """Restituisce i metri per grado di longitudine alla latitudine data.

    Lacuna 14: il vecchio fattore fisso 79_000 m/grado era calibrato per
    l'Italia settentrionale (lat ~44°, cos(44°)≈0.719, 111_000*0.719≈79_900)
    e produceva dimensioni pixel errate per aree fuori dall'ER.
    Questa funzione usa la latitudine reale del bbox e rimane corretta
    ovunque in Italia e oltre.

    Esempio: lat 44° -> ~79_900 m/grado; lat 37° (Sicilia) -> ~88_700 m/grado.
    """
    import math
    return _M_PER_GRADO_LAT * math.cos(math.radians(lat_centrale))


def dimensioni_pixel_da_bbox(
    bbox_wgs84: tuple[float, float, float, float],
    risoluzione_m: float,
    pixel_max_lato: int = 4096,
) -> tuple[int, int]:
    """Calcola (width, height) in pixel per un bbox WGS84 e una risoluzione
    in metri/pixel, usando la latitudine centrale del bbox per la conversione
    longitudine→metri (non un fattore fisso hardcoded).

    Usato da scarica_sentinel2_automatico, scarica_sentinel2_10bande e
    fonte_geoportale_er per sostituire il calcolo inline duplicato.
    """
    minx, miny, maxx, maxy = bbox_wgs84
    lat_c = (miny + maxy) / 2.0
    larghezza_m = (maxx - minx) * metri_per_grado_lon(lat_c)
    altezza_m = (maxy - miny) * _M_PER_GRADO_LAT
    width = max(16, min(pixel_max_lato, int(larghezza_m / risoluzione_m)))
    height = max(16, min(pixel_max_lato, int(altezza_m / risoluzione_m)))
    return width, height

EVALSCRIPT_RED_NIR = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: { bands: 3, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.B04, sample.B08, sample.dataMask];
}
"""


@dataclass
class CredenzialiCopernicus:
    client_id: str
    client_secret: str

    @staticmethod
    def da_ambiente() -> "CredenzialiCopernicus":
        cid = os.environ.get("COPERNICUS_CLIENT_ID")
        secret = os.environ.get("COPERNICUS_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError(
                "Credenziali Copernicus mancanti. Imposta le variabili "
                "d'ambiente COPERNICUS_CLIENT_ID e COPERNICUS_CLIENT_SECRET "
                "(gratuite, registrazione una tantum su "
                "https://dataspace.copernicus.eu)."
            )
        return CredenzialiCopernicus(cid, secret)


def _ottieni_token(cred: CredenzialiCopernicus) -> str:
    resp = requests.post(
        CDSE_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def scarica_sentinel2_automatico(
    bbox_wgs84: tuple[float, float, float, float],
    data_centrale: str,
    path_output: str,
    cred: CredenzialiCopernicus | None = None,
    finestra_giorni: int = 15,
    copertura_nuvole_max: int = 30,
) -> str:
    """Scarica automaticamente rosso (B04) + NIR (B08) Sentinel-2 L2A per il
    bbox dato (lon/lat, WGS84), cercando la scena più vicina a
    `data_centrale` (formato YYYY-MM-DD) entro +/- `finestra_giorni` con
    copertura nuvolosa sotto `copertura_nuvole_max`%. Scrive un GeoTIFF a 2
    bande (red, nir) in `path_output` e ne restituisce il percorso.

    Nessuna interazione manuale richiesta oltre alle credenziali in
    ambiente/parametro.
    """
    cred = cred or CredenzialiCopernicus.da_ambiente()
    token = _ottieni_token(cred)

    from datetime import datetime, timedelta

    centro = datetime.fromisoformat(data_centrale)
    inizio = (centro - timedelta(days=finestra_giorni)).strftime("%Y-%m-%dT00:00:00Z")
    fine = (centro + timedelta(days=finestra_giorni)).strftime("%Y-%m-%dT23:59:59Z")

    minx, miny, maxx, maxy = bbox_wgs84
    # Lacuna 14: usa dimensioni_pixel_da_bbox con latitudine reale invece
    # del fattore fisso 79_000 m/grado (valido solo per l'Italia settentrionale).
    width, height = dimensioni_pixel_da_bbox(bbox_wgs84, RISOLUZIONE_SENTINEL2_M)

    payload = {
        "input": {
            "bounds": {
                "bbox": [minx, miny, maxx, maxy],
                "properties": {
                    "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                },
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": inizio, "to": fine},
                        "maxCloudCoverage": copertura_nuvole_max,
                        "mosaickingOrder": "leastCC",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": EVALSCRIPT_RED_NIR,
    }

    resp = requests.post(
        CDSE_PROCESS_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    resp.raise_for_status()

    with open(path_output, "wb") as f:
        f.write(resp.content)

    # Lacuna 13: verifica che le bande siano riflettanza 0-1 e non DN 0-10000.
    verifica_riflettanza_0_1(path_output, nome="Sentinel-2 2bande")
    return path_output


EVALSCRIPT_10_BANDE = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12", "dataMask"],
    output: { bands: 11, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.B02, sample.B03, sample.B04, sample.B05, sample.B06,
          sample.B07, sample.B08, sample.B8A, sample.B11, sample.B12,
          sample.dataMask];
}
"""

# Ordine delle bande cosi' come scritte da EVALSCRIPT_10_BANDE (e atteso da
# super_res.py / SEN2SRLite). Le bande B05/B06/B07/B8A/B11/B12 sono native a
# 20 m/pixel: la Process API le ricampiona automaticamente alla risoluzione
# di output richiesta (qui 10 m, come B02/B03/B04/B08), quindi il GeoTIFF
# risultante ha gia' tutte le bande sulla stessa griglia a 10 m.
ORDINE_BANDE_10 = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]


def scarica_sentinel2_10bande(
    bbox_wgs84: tuple[float, float, float, float],
    data_centrale: str,
    path_output: str,
    cred: CredenzialiCopernicus | None = None,
    finestra_giorni: int = 15,
    copertura_nuvole_max: int = 30,
) -> str:
    """Come scarica_sentinel2_automatico, ma scarica tutte le 10 bande
    necessarie a SEN2SR/SEN2SRLite per la super-risoluzione (modalita
    "alta qualita"), invece delle sole Red+NIR. Scrive un GeoTIFF a 11
    bande (10 bande + dataMask, stesso ordine di ORDINE_BANDE_10) in
    `path_output`.

    Scarica ~5 volte piu' dati della modalita standard (10 bande contro 2):
    tenerne conto per i limiti di quota Copernicus."""
    cred = cred or CredenzialiCopernicus.da_ambiente()
    token = _ottieni_token(cred)

    from datetime import datetime, timedelta

    centro = datetime.fromisoformat(data_centrale)
    inizio = (centro - timedelta(days=finestra_giorni)).strftime("%Y-%m-%dT00:00:00Z")
    fine = (centro + timedelta(days=finestra_giorni)).strftime("%Y-%m-%dT23:59:59Z")

    minx, miny, maxx, maxy = bbox_wgs84
    # Lacuna 14: usa dimensioni_pixel_da_bbox con latitudine reale.
    width, height = dimensioni_pixel_da_bbox(bbox_wgs84, RISOLUZIONE_SENTINEL2_M)

    payload = {
        "input": {
            "bounds": {
                "bbox": [minx, miny, maxx, maxy],
                "properties": {
                    "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                },
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": inizio, "to": fine},
                        "maxCloudCoverage": copertura_nuvole_max,
                        "mosaickingOrder": "leastCC",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": EVALSCRIPT_10_BANDE,
    }

    resp = requests.post(
        CDSE_PROCESS_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    resp.raise_for_status()

    with open(path_output, "wb") as f:
        f.write(resp.content)

    # Lacuna 13: stessa verifica del percorso a 2 bande.
    verifica_riflettanza_0_1(path_output, nome="Sentinel-2 10bande")
    return path_output


def _tile_dem_per_cella(lat_int: int, lon_int: int) -> str:
    """Nome del tile Copernicus DEM GLO-30 (1x1 grado) che copre la cella
    identificata dagli interi di lat/lon in basso a sinistra."""
    ns = "N" if lat_int >= 0 else "S"
    ew = "E" if lon_int >= 0 else "W"
    lat_s = f"{abs(lat_int):02d}"
    lon_s = f"{abs(lon_int):03d}"
    nome = f"Copernicus_DSM_COG_10_{ns}{lat_s}_00_{ew}{lon_s}_00_DEM"
    return f"{COPERNICUS_DEM_BASE}/{nome}/{nome}.tif"


def scarica_copernicus_dem(
    bbox_wgs84: tuple[float, float, float, float],
    path_output: str,
) -> str:
    """Scarica (accesso pubblico anonimo, nessuna credenziale) i tile
    Copernicus DEM GLO-30 (30 m/pixel) che coprono il bbox, li fonde e li
    ritaglia, salvando un GeoTIFF unico in `path_output`."""
    minx, miny, maxx, maxy = bbox_wgs84

    lat_min_int = math.floor(miny)
    lat_max_int = math.floor(maxy)
    lon_min_int = math.floor(minx)
    lon_max_int = math.floor(maxx)

    datasets = []
    memfiles = []
    try:
        for lat_i in range(lat_min_int, lat_max_int + 1):
            for lon_i in range(lon_min_int, lon_max_int + 1):
                url = _tile_dem_per_cella(lat_i, lon_i)
                r = requests.get(url, timeout=60)
                if r.status_code != 200:
                    continue  # tile non esistente (es. mare) -> salta
                mem = MemoryFile(r.content)
                memfiles.append(mem)
                datasets.append(mem.open())

        if not datasets:
            raise RuntimeError(
                "Nessun tile Copernicus DEM trovato per l'area richiesta."
            )

        mosaico, trasform = rio_merge(datasets)
        profilo = datasets[0].profile.copy()
        profilo.update(
            height=mosaico.shape[1],
            width=mosaico.shape[2],
            transform=trasform,
        )
        with rasterio.open(path_output, "w", **profilo) as dst:
            dst.write(mosaico)
    finally:
        for d in datasets:
            d.close()
        for m in memfiles:
            m.close()

    return path_output


def verifica_riflettanza_0_1(path_tif: str, nome: str = "") -> None:
    """Lacuna 13: verifica che i valori del GeoTIFF siano nel range 0-1
    (riflettanza float, come restituito dall'evalscript con sampleType
    FLOAT32). Se Copernicus cambia il sampleType di default e restituisce
    DN 0-10000, il calcolo NDVI produce risultati compressi vicino a 0
    senza errori visibili: qui lo rileviamo e avvisiamo esplicitamente.

    Controlla solo un campione (prima banda, campione di pixel validi) per
    non rallentare il flusso principale.
    """
    with rasterio.open(path_tif) as src:
        # Leggi solo la prima banda, finestra centrale piccola
        import math
        cx = src.width // 2
        cy = src.height // 2
        metà = max(4, min(32, src.width // 8, src.height // 8))
        from rasterio.windows import Window
        win = Window(
            max(0, cx - metà), max(0, cy - metà),
            min(metà * 2, src.width), min(metà * 2, src.height),
        )
        campione = src.read(1, window=win).astype("float32").ravel()
        campione = campione[np.isfinite(campione)]
        if campione.size == 0:
            return  # tutto nodata, niente da verificare
        valore_max = float(campione.max())
        valore_medio = float(campione.mean())

    prefisso = f"[{nome}] " if nome else ""
    if valore_max > 10.0:
        # Con DN 0-10000, il massimo tipico è nell'ordine di 2000-8000
        raise RuntimeError(
            f"{prefisso}ATTENZIONE CRITICA: il GeoTIFF '{path_tif}' sembra contenere "
            f"DN (Digital Numbers, max rilevato: {valore_max:.0f}) invece di riflettanza "
            "0-1 (max atteso < 1.0 per vegetazione/suolo tipici). "
            "Copernicus potrebbe aver cambiato il sampleType di default nell'evalscript. "
            "Il calcolo NDVI sarebbe errato: controllare l'evalscript e il profilo del file."
        )
    elif valore_max > 2.0:
        # Range 1-10: anomalo ma non DN classici — avviso non bloccante
        print(
            f"{prefisso}ATTENZIONE: '{path_tif}' ha valori fino a {valore_max:.2f} "
            "(atteso 0-1 per riflettanza). Potrebbe essere un'area con anomalie o "
            "un cambio di formato. Verifica prima di fidarti dell'NDVI."
        )
    else:
        # Range atteso: log solo in debug (non vogliamo verbosità in produzione)
        pass  # OK


def bbox_da_buffer(geom_buffer_gdf) -> tuple[float, float, float, float]:
    """Converte il GeoDataFrame del buffer di analisi (in qualunque CRS
    proiettato) nel bbox WGS84 (lon/lat) richiesto dalle API Copernicus."""
    bounds = geom_buffer_gdf.total_bounds
    return transform_bounds(geom_buffer_gdf.crs, "EPSG:4326", *bounds)
