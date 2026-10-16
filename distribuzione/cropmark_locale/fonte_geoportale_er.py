#!/usr/bin/env python3
"""
fonte_geoportale_er.py
=======================
Download automatico di ortofoto e DTM dal Geoportale della Regione
Emilia-Romagna (servizi WMS pubblici, nessuna credenziale), per il flusso
--auto di rileva_segni.py.

Risoluzione molto più alta della modalità satellitare di fonte_satellitare.py
(Sentinel-2 10 m/pixel, Copernicus DEM 30 m/pixel):
  - Ortofoto AGEA 2020 R+G+B+NIR: 20 cm/pixel, copertura intera regione
    (fusa da due servizi WMS separati, RGB e NIR — vedi scarica_ortofoto_er).
  - DTM 5x5: 5 m/pixel, copertura intera regione.

Verificati esistenti e più recenti ma con copertura PARZIALE (non ancora
estesa a tutta la regione al momento della scrittura, verificare caso per
caso con `disponibile_er_alta_risoluzione`):
  - Ortofoto RER 2022 RGB/NIR: 10 cm/pixel circa.
  - DTM RER 2022 (LiDAR): 0.5 m/pixel.

Questo modulo NON copre i geoportali di altre regioni italiane né il
Portale Cartografico Nazionale (DTM nazionale: solo 20 m/pixel, più
grossolano del DTM 5x5 regionale; i LiDAR nazionali del Piano
Straordinario non risultano accessibili via WMS diretto, solo tramite
portale di download). Fuori Emilia-Romagna, usare `fonte_satellitare.py`.

Nessuna credenziale richiesta per nessuna di queste chiamate.
"""

from __future__ import annotations

import math
import time

import numpy as np
import rasterio
from rasterio.io import MemoryFile

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Il modulo 'requests' è necessario per il download automatico: "
        "pip install requests"
    ) from exc


WMS_BASE = "https://servizigis.regione.emilia-romagna.it/wms"

# bbox WGS84 (lon_min, lat_min, lon_max, lat_max) e risoluzione nativa dei
# servizi verificati. Aggiornare se la Regione pubblica nuove coperture.
# L'ortofoto AGEA2020 è pubblicata come due servizi separati con le stesse
# bande RGB e NIR rispettivamente (stesso bbox, stessa risoluzione): vanno
# fusi in un unico GeoTIFF a 4 bande (R, G, B, NIR) per poter calcolare
# l'NDVI, che richiede sempre Red+NIR nello stesso raster.
ORTOFOTO_AGEA2020_RGB = {
    "layer": "Agea2020_RGB",
    "servizio": "agea2020_rgb",
    "risoluzione_m": 0.2,
    "bbox_wgs84": (9.162128, 43.701243, 12.868166, 45.153986),
}

ORTOFOTO_AGEA2020_NIR = {
    "layer": "Agea2020_NIR",
    "servizio": "agea2020_nir",
    "risoluzione_m": 0.2,
    "bbox_wgs84": (9.162128, 43.701243, 12.868166, 45.153986),
}

ORTOFOTO_AGEA2020 = {
    **ORTOFOTO_AGEA2020_RGB,
    "etichetta": "ortofoto AGEA 2020 R+G+B+NIR (geoportale ER, 20 cm/pixel, bande fuse da due servizi WMS)",
}

DTM_5X5 = {
    "layer": "Dtm5x5",
    "servizio": "dtm5x5",
    "risoluzione_m": 5.0,
    "bbox_wgs84": (9.101834, 43.678269, 12.849044, 45.171433),
    "etichetta": "DTM 5x5 (geoportale ER, 5 m/pixel)",
}

# Il WMS della Regione risponde 400 Bad Request (non un'eccezione WMS, un
# rifiuto a livello di proxy/WAF davanti al servizio) oltre una certa
# dimensione — verificato empiricamente: 3200px passa, 3500px no. 3000 come
# tetto con margine, non 4000 (bastava un buffer di analisi non piccolissimo
# a 20cm/pixel per superarlo e far fallire l'intera analisi).
PIXEL_MAX_LATO = 3000


def _bbox_contiene(bbox_esterno, bbox_interno) -> bool:
    ext_minx, ext_miny, ext_maxx, ext_maxy = bbox_esterno
    minx, miny, maxx, maxy = bbox_interno
    return (
        ext_minx <= minx and ext_miny <= miny
        and ext_maxx >= maxx and ext_maxy >= maxy
    )


def disponibile_ortofoto_er(bbox_wgs84: tuple[float, float, float, float]) -> bool:
    """True se il bbox è interamente coperto dall'ortofoto AGEA2020."""
    return _bbox_contiene(ORTOFOTO_AGEA2020["bbox_wgs84"], bbox_wgs84)


def disponibile_dtm_er(bbox_wgs84: tuple[float, float, float, float]) -> bool:
    """True se il bbox è interamente coperto dal DTM 5x5."""
    return _bbox_contiene(DTM_5X5["bbox_wgs84"], bbox_wgs84)


def disponibile_er_alta_risoluzione(bbox_wgs84: tuple[float, float, float, float]) -> bool:
    """True se il bbox richiesto è interamente coperto sia dall'ortofoto
    AGEA2020 sia dal DTM 5x5 (i due servizi a copertura regionale completa
    verificati in questo modulo)."""
    return disponibile_ortofoto_er(bbox_wgs84) and disponibile_dtm_er(bbox_wgs84)


def _dimensioni_pixel(bbox_wgs84, risoluzione_m: float) -> tuple[int, int]:
    # Lacuna 14: delega a dimensioni_pixel_da_bbox in fonte_satellitare,
    # che usa la latitudine reale del bbox invece del fattore fisso 79_000.
    from fonte_satellitare import dimensioni_pixel_da_bbox
    w, h = dimensioni_pixel_da_bbox(bbox_wgs84, risoluzione_m, pixel_max_lato=PIXEL_MAX_LATO)
    # Il minimo per il geoportale ER è 8px invece di 16 (bbox molto piccoli).
    return max(8, w), max(8, h)


def _wms_getmap_geotiff(servizio: str, layer: str, bbox_wgs84, risoluzione_m: float, path_output: str) -> str:
    contenuto = _wms_getmap_bytes(servizio, layer, bbox_wgs84, risoluzione_m)
    with open(path_output, "wb") as f:
        f.write(contenuto)
    return path_output


def _wms_getmap_bytes(servizio: str, layer: str, bbox_wgs84, risoluzione_m: float) -> bytes:
    """Un tentativo singolo è inaffidabile: il WMS della Regione a volte
    risponde con un'eccezione XML transitoria, o va in timeout di rete,
    anche per bbox interamente dentro copertura e con la stessa identica
    richiesta riuscita al tentativo successivo (osservato empiricamente:
    prima un'eccezione XML, poi — dopo aver aggiunto un retry solo per
    quel caso — un ReadTimeout, perché un'eccezione di rete sulla
    `requests.get` stessa usciva dalla funzione senza mai arrivare al
    controllo del Content-Type sotto). Tre tentativi con una breve pausa,
    catturando ESPLICITAMENTE anche le eccezioni di rete — non solo la
    risposta XML malformata — stesso approccio già usato per i tile
    satellite Google in report_pdf.py."""
    minx, miny, maxx, maxy = bbox_wgs84
    width, height = _dimensioni_pixel(bbox_wgs84, risoluzione_m)
    ultimo_errore = None
    for tentativo in range(3):
        try:
            resp = requests.get(
                f"{WMS_BASE}/{servizio}",
                params={
                    "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                    "LAYERS": layer, "STYLES": "", "CRS": "CRS:84",
                    "BBOX": f"{minx},{miny},{maxx},{maxy}",
                    "WIDTH": width, "HEIGHT": height, "FORMAT": "image/tiff",
                },
                timeout=60,
            )
            resp.raise_for_status()
            if resp.headers.get("Content-Type", "").startswith("image/"):
                return resp.content
            ultimo_errore = resp.text[:500]
        except requests.exceptions.RequestException as e:
            ultimo_errore = str(e)
        if tentativo < 2:
            time.sleep(1.5)
    raise RuntimeError(
        f"Risposta inattesa da {servizio} dopo 3 tentativi (probabile bbox "
        f"fuori copertura, parametri non validi, o servizio regionale "
        f"temporaneamente non raggiungibile): {ultimo_errore}"
    )


def scarica_ortofoto_er(bbox_wgs84: tuple[float, float, float, float], path_output: str) -> str:
    """Scarica e fonde ortofoto AGEA2020 RGB + NIR (20 cm/pixel) sul bbox
    dato, in un unico GeoTIFF a 4 bande (R, G, B, NIR) pronto per il calcolo
    dell'NDVI (--banda-red 1 --banda-nir 4). Nessuna credenziale richiesta.

    Il servizio "NIR" della Regione pubblica in realtà un composito
    falso-colore CIR: banda1=NIR vero, banda2=Red (identico alla banda1 del
    servizio RGB, verificato per confronto pixel-a-pixel), banda3=Green. Qui
    si usa solo la banda1 (NIR vero) da quel servizio, scartando le altre
    due perché duplicate di quanto già fornito dal servizio RGB.
    """
    bytes_rgb = _wms_getmap_bytes(
        ORTOFOTO_AGEA2020_RGB["servizio"], ORTOFOTO_AGEA2020_RGB["layer"],
        bbox_wgs84, ORTOFOTO_AGEA2020_RGB["risoluzione_m"],
    )
    bytes_nir = _wms_getmap_bytes(
        ORTOFOTO_AGEA2020_NIR["servizio"], ORTOFOTO_AGEA2020_NIR["layer"],
        bbox_wgs84, ORTOFOTO_AGEA2020_NIR["risoluzione_m"],
    )

    with MemoryFile(bytes_rgb) as mem_rgb, MemoryFile(bytes_nir) as mem_nir:
        with mem_rgb.open() as src_rgb, mem_nir.open() as src_nir:
            if src_rgb.shape != src_nir.shape:
                raise RuntimeError(
                    "I servizi RGB e NIR di AGEA2020 hanno restituito dimensioni "
                    f"diverse per lo stesso bbox ({src_rgb.shape} vs {src_nir.shape}): "
                    "non fondibili in modo affidabile."
                )
            # Lacuna 7: verifica anche il transform (origine e pixel size),
            # non solo le dimensioni in pixel. Shape identica ma transform
            # diverso (es. versioni diverse del servizio o caching del proxy)
            # produrrebbe un fuso silenziosamente disallineato e un NDVI errato.
            t_rgb = src_rgb.transform
            t_nir = src_nir.transform
            _TOL = 1e-8  # gradi: ~1 mm a qualsiasi latitudine
            if (
                abs(t_rgb.c - t_nir.c) > _TOL or  # origine X (lon)
                abs(t_rgb.f - t_nir.f) > _TOL or  # origine Y (lat)
                abs(t_rgb.a - t_nir.a) > _TOL or  # pixel width
                abs(t_rgb.e - t_nir.e) > _TOL      # pixel height
            ):
                raise RuntimeError(
                    "I servizi RGB e NIR di AGEA2020 hanno restituito transform diversi "
                    f"per lo stesso bbox (RGB: {t_rgb} | NIR: {t_nir}): "
                    "il fuso sarebbe spazialmente disallineato. "
                    "Probabile cambio di versione o risposta in cache del proxy regionale."
                )
            r, g, b = src_rgb.read(1), src_rgb.read(2), src_rgb.read(3)
            nir = src_nir.read(1)
            stack = np.stack([r, g, b, nir])

            profilo = src_rgb.profile.copy()
            profilo.update(count=4, dtype=stack.dtype)
            with rasterio.open(path_output, "w", **profilo) as dst:
                dst.write(stack)

    return path_output


def scarica_dtm_er(bbox_wgs84: tuple[float, float, float, float], path_output: str) -> str:
    """Scarica il DTM 5x5 (5 m/pixel) sul bbox dato. Nessuna credenziale
    richiesta."""
    return _wms_getmap_geotiff(
        DTM_5X5["servizio"], DTM_5X5["layer"], bbox_wgs84,
        DTM_5X5["risoluzione_m"], path_output,
    )
