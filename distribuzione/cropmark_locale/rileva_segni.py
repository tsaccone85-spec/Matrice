#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
rileva_segni.py
================
Strumento per il rilevamento di anomalie compatibili con segni archeologici
(crop mark, soil mark, shadow mark) in un buffer attorno a un'area di cantiere.

INPUT
-----
1. GeoPackage con il poligono dell'area di cantiere (1 layer, 1+ feature)
2. Una o più ortofoto multibanda, ciascuna con la propria data di acquisizione
   (deve includere almeno Red e NIR per calcolare NDVI) - GeoTIFF georeferenziato,
   stesso CRS consigliato (riproiettato se serve). Più date nell'arco dell'anno
   permettono di confermare un segno vegetazionale su più stadi colturali,
   invece di basarsi su un singolo scatto che può essere fuorviante (un segno
   visibile solo in un momento dell'anno è meno affidabile di uno che si
   ripete su più acquisizioni).
3. DTM LiDAR (GeoTIFF a singola banda, quote in metri) - il microrilievo non
   varia stagionalmente, quindi basta un solo DTM.

OUTPUT
------
GeoPackage con due layer:
 - "buffer_analisi": il buffer di analisi attorno al cantiere
 - "segni_rilevati": poligoni/linee delle anomalie rilevate, con attributi:
     tipo ("vegetazionale" | "microrilievo"), score, area_mq, geometria

LOGICA
------
- Vegetazionale: NDVI nel buffer -> standardizzazione locale (z-score) ->
  sogliatura sulle code (alta o bassa vegetazione anomala) -> filtro per
  geometria (linearità/rettangolarità) tramite regionprops di skimage.
- Microrilievo: DTM nel buffer -> slope + hillshade multi-direzionale ->
  sogliatura sulle anomalie locali (residuo rispetto a superficie di trend
  a bassa frequenza, tipo "detrending") -> stessa pipeline di filtro
  geometrico.

Questo è uno strumento di SEGNALAZIONE, non di certificazione: ogni poligono
in output è un candidato da validare da un operatore (vedi campo "score" e
"note" per capire perché è stato segnalato).

USO
---
python3 rileva_segni.py \
    --cantiere area_cantiere.gpkg \
    --ortofoto marzo.tif 2026-03-15 \
    --ortofoto luglio.tif 2026-07-20 \
    --dtm dtm.tif \
    --buffer-m 500 \
    --banda-red 1 --banda-nir 4 \
    --output segni_rilevati.gpkg \
    --output-pdf report.pdf
"""

import argparse
import os
import sys
import warnings
from dataclasses import dataclass, field

# Lacuna 11: MASSIMO_STAGIONI importata da fonte_satellitare (unica sorgente
# di verità) invece di essere ridefinita localmente con rischio di divergenza.
from fonte_satellitare import MASSIMO_STAGIONI as _MASSIMO_STAGIONI_IMPORT

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.features import shapes as rio_shapes
from shapely.geometry import shape as shapely_shape, mapping
from shapely.ops import unary_union
from scipy import ndimage as ndi
from skimage.measure import label, regionprops
from skimage.filters import gaussian

warnings.filterwarnings("ignore", category=RuntimeWarning)


# --------------------------------------------------------------------------
# Parametri di analisi (esposti qui in cima per essere facilmente tarabili)
# --------------------------------------------------------------------------

@dataclass
class ParametriAnalisi:
    buffer_m: float = 500.0

    # Vegetazionale (NDVI)
    ndvi_sigma_smooth: float = 1.0      # smoothing gaussiano in pixel
    ndvi_zscore_soglia: float = 1.8     # soglia |z| per considerare anomalia
    ndvi_min_area_mq: float = 4.0       # area minima poligono (rumore via)

    # Microrilievo (DTM)
    dtm_trend_sigma: float = 15.0       # sigma gaussiano per stimare il trend
                                         # a bassa frequenza (superficie "liscia")
    dtm_residuo_soglia_m: float = 0.08  # soglia residuo (m) oltre il trend
    dtm_min_area_mq: float = 4.0

    # Filtro geometrico comune: scarta macchie troppo "tonde"/informi,
    # privilegia forme allungate o ad angoli retti (tipiche di strutture)
    eccentricita_min: float = 0.5       # 0 = cerchio perfetto, 1 = linea
    solidity_max: float = 0.95          # forme molto "piene e tonde" via


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------

def leggi_area_cantiere(path_gpkg: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path_gpkg)
    if gdf.empty:
        raise ValueError(f"Il GeoPackage '{path_gpkg}' non contiene geometrie.")
    if gdf.crs is None:
        raise ValueError(
            f"Il GeoPackage '{path_gpkg}' non ha un CRS definito. "
            "Impostane uno prima di procedere (es. EPSG:32632 per UTM 32N Italia)."
        )
    return gdf


def crea_buffer(gdf_cantiere: gpd.GeoDataFrame, buffer_m: float) -> gpd.GeoDataFrame:
    """Crea il buffer di analisi. Se il CRS è geografico (lat/lon), riproietta
    in un CRS metrico (UTM) per poter bufferizzare in metri in modo corretto."""
    crs_originale = gdf_cantiere.crs
    if crs_originale.is_geographic:
        centro = gdf_cantiere.union_all().centroid
        utm_epsg = calcola_utm_epsg(centro.x, centro.y)
        gdf_metrico = gdf_cantiere.to_crs(epsg=utm_epsg)
    else:
        gdf_metrico = gdf_cantiere
        utm_epsg = gdf_cantiere.crs.to_epsg()

    unione = gdf_metrico.union_all()
    buffer_geom = unione.buffer(buffer_m)
    gdf_buffer = gpd.GeoDataFrame(
        {"tipo": ["buffer_analisi"], "raggio_m": [buffer_m]},
        geometry=[buffer_geom],
        crs=gdf_metrico.crs,
    )
    return gdf_buffer


def calcola_utm_epsg(lon: float, lat: float) -> int:
    zona = int((lon + 180) / 6) + 1
    if lat >= 0:
        return 32600 + zona
    return 32700 + zona


def _riproietta_a_crs_metrico(path_input: str, crs_destinazione) -> str:
    """I servizi WMS/Process API usati per il download automatico (geoportale ER,
    Sentinel-2, Copernicus DEM) restituiscono i GeoTIFF georeferenziati in
    EPSG:4326 (gradi), non in un CRS metrico. Se non corretto, il transform in
    gradi produce un'area per pixel in "metri quadri" vicina a zero (un grado
    non è un metro), e il filtro geometrico su area_mq in
    estrai_poligoni_da_maschera scarta SEMPRE ogni candidato, qualunque sia la
    soglia di z-score: risultato "0 anomalie" falso, non un vero esito
    negativo. Riproiettare nel CRS metrico del buffer (stesso usato per
    l'analisi) prima di procedere."""
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling

    with rasterio.open(path_input) as src:
        if src.crs is not None and src.crs == crs_destinazione:
            return path_input
        transform, width, height = calculate_default_transform(
            src.crs, crs_destinazione, src.width, src.height, *src.bounds
        )
        profilo = src.profile.copy()
        profilo.update(crs=crs_destinazione, transform=transform, width=width, height=height)

        path_output = path_input.rsplit(".", 1)[0] + "_metrico.tif"
        with rasterio.open(path_output, "w", **profilo) as dst:
            for banda in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, banda),
                    destination=rasterio.band(dst, banda),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=crs_destinazione,
                    resampling=Resampling.bilinear,
                )
    return path_output


def genera_date_automatiche(n: int) -> list[str]:
    """N date (YYYY-MM-DD) distribuite nell'ultimo anno, a passo costante
    (oggi, oggi-365/n giorni, oggi-2*365/n giorni, …), dalla più vecchia
    alla più recente: l'utente sceglie solo QUANTE acquisizioni usare
    (--num-acquisizioni, 2-5), non le date esatte — più N è alto, più la
    copertura stagionale è fitta. Richiesta di Tommaso, 2026-10-07."""
    from datetime import datetime, timedelta
    oggi = datetime.now()
    passo_giorni = 365 / n
    date = [oggi - timedelta(days=round(passo_giorni * i)) for i in range(n)]
    return sorted(d.strftime("%Y-%m-%d") for d in date)


def _scarica_sentinel2_alta_qualita(bbox_wgs84, data_str, cartella_tmp, cred) -> str:
    """Scarica le 10 bande Sentinel-2 e applica la super-risoluzione
    (SEN2SRLite). Se le librerie non sono installate sulla macchina
    dell'utente (modalita "standard" scelta all'installazione), avvisa e
    ricade automaticamente sulla modalita standard (Red+NIR a 10m) invece
    di interrompere l'analisi."""
    import os
    import fonte_satellitare as satellitare

    path_10bande = os.path.join(cartella_tmp, f"auto_sentinel2_10b_{data_str}.tif")
    satellitare.scarica_sentinel2_10bande(bbox_wgs84, data_str, path_10bande, cred=cred)

    try:
        import super_res
        path_output = os.path.join(cartella_tmp, f"auto_sentinel2_superres_{data_str}.tif")
        return super_res.super_risolvi_a_red_nir(path_10bande, path_output)
    except super_res.SuperResNonDisponibile as e:
        print(f"       ATTENZIONE: {e}")
        print("       Ricado sulla modalita standard (10 m/pixel) per questa data.")
        path_output = os.path.join(cartella_tmp, f"auto_sentinel2_{data_str}.tif")
        satellitare.scarica_sentinel2_automatico(bbox_wgs84, data_str, path_output, cred=cred)
        return path_output


def scarica_dati_automatici(
    geom_buffer, crs_buffer, date_richieste: list[str], cartella_tmp: str,
    qualita: str = "standard",
) -> tuple[list[tuple[str, str]], str]:
    """Scarica automaticamente ortofoto+DTM per il buffer dato, preferendo il
    Geoportale Emilia-Romagna (ortofoto AGEA2020 20 cm/pixel + DTM5x5 5
    m/pixel, nessuna credenziale) quando il buffer è interamente coperto;
    altrimenti ricade su Sentinel-2 + Copernicus DEM (fonte_satellitare.py),
    molto più grossolani (10 m / 30 m/pixel) e che richiedono credenziali
    Copernicus per Sentinel-2.

    Ritorna (ortofoto_date, path_dtm) pronti per la pipeline esistente.
    """
    import os
    import fonte_geoportale_er as geoportale_er
    import fonte_satellitare as satellitare

    os.makedirs(cartella_tmp, exist_ok=True)
    bbox_wgs84 = satellitare.bbox_da_buffer(
        gpd.GeoDataFrame(geometry=[geom_buffer], crs=crs_buffer)
    )

    multitemporale_richiesto = len(date_richieste) >= 2

    if not multitemporale_richiesto and geoportale_er.disponibile_er_alta_risoluzione(bbox_wgs84):
        print("       Fonte automatica: Geoportale Emilia-Romagna "
              "(ortofoto AGEA2020 20cm + DTM5x5 5m, alta risoluzione)")
        path_ortofoto = os.path.join(cartella_tmp, "auto_ortofoto_er.tif")
        path_dtm = os.path.join(cartella_tmp, "auto_dtm_er.tif")
        geoportale_er.scarica_ortofoto_er(bbox_wgs84, path_ortofoto)
        geoportale_er.scarica_dtm_er(bbox_wgs84, path_dtm)
        path_ortofoto = _riproietta_a_crs_metrico(path_ortofoto, crs_buffer)
        path_dtm = _riproietta_a_crs_metrico(path_dtm, crs_buffer)
        etichetta = geoportale_er.ORTOFOTO_AGEA2020["etichetta"]
        ortofoto_date = [(path_ortofoto, etichetta)]
        return ortofoto_date, path_dtm

    if multitemporale_richiesto:
        print("       Analisi multi-temporale richiesta (più date): il Geoportale ER ha "
              "una sola acquisizione ortofoto (AGEA2020, nessuna stagionalità), quindi per "
              "l'ortofoto si usa Sentinel-2 (10 m/pixel, automatico, precisione molto "
              "inferiore: un fossato o muro sepolto largo 1-2 m NON è risolvibile).")
    else:
        print("       ATTENZIONE: buffer fuori copertura Geoportale Emilia-Romagna "
              "(o parzialmente fuori). Ricado sulla modalità satellitare "
              "(Sentinel-2 10 m/pixel): precisione molto inferiore, un fossato o muro "
              "sepolto largo 1-2 m NON è risolvibile a questa risoluzione.")

    cred = satellitare.CredenzialiCopernicus.da_ambiente()
    ortofoto_date = []
    for data_str in date_richieste:
        if qualita == "alta":
            path_ortofoto = _scarica_sentinel2_alta_qualita(
                bbox_wgs84, data_str, cartella_tmp, cred
            )
            etichetta = f"{data_str} (Sentinel-2 + super-risoluzione IA, ~2.5 m/pixel, automatico)"
        else:
            path_ortofoto = os.path.join(cartella_tmp, f"auto_sentinel2_{data_str}.tif")
            satellitare.scarica_sentinel2_automatico(bbox_wgs84, data_str, path_ortofoto, cred=cred)
            etichetta = f"{data_str} (Sentinel-2, 10 m/pixel, automatico)"
        path_ortofoto = _riproietta_a_crs_metrico(path_ortofoto, crs_buffer)
        ortofoto_date.append((path_ortofoto, etichetta))

    if geoportale_er.disponibile_dtm_er(bbox_wgs84):
        print("       DTM: Geoportale ER (DTM5x5, 5 m/pixel, migliore di Copernicus DEM)")
        path_dtm = os.path.join(cartella_tmp, "auto_dtm_er.tif")
        geoportale_er.scarica_dtm_er(bbox_wgs84, path_dtm)
    else:
        print("       DTM: Copernicus DEM (30 m/pixel, fuori copertura ER)")
        path_dtm = os.path.join(cartella_tmp, "auto_copernicus_dem.tif")
        satellitare.scarica_copernicus_dem(bbox_wgs84, path_dtm)
    path_dtm = _riproietta_a_crs_metrico(path_dtm, crs_buffer)

    return ortofoto_date, path_dtm


def ritaglia_raster(path_raster: str, geom_buffer, crs_buffer) -> tuple[np.ndarray, dict]:
    """Ritaglia un raster sul buffer, riproiettando la geometria se necessario."""
    with rasterio.open(path_raster) as src:
        geom_gdf = gpd.GeoDataFrame(geometry=[geom_buffer], crs=crs_buffer)
        if src.crs != crs_buffer:
            geom_gdf = geom_gdf.to_crs(src.crs)
        geom_riproiettata = [mapping(geom_gdf.geometry.iloc[0])]

        try:
            out_image, out_transform = rio_mask(
                src, geom_riproiettata, crop=True, filled=True, nodata=src.nodata
            )
        except ValueError as e:
            raise ValueError(
                f"Il buffer non interseca il raster '{path_raster}'. "
                "Controlla che copra l'area di cantiere."
            ) from e

        profilo = src.profile.copy()
        profilo.update(
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
        )
        return out_image, profilo


def calcola_ndvi(stack: np.ndarray, banda_red: int, banda_nir: int, nodata=None) -> np.ndarray:
    """stack ha shape (bande, righe, colonne), indici banda 1-based come da convenzione utente."""
    red = stack[banda_red - 1].astype("float32")
    nir = stack[banda_nir - 1].astype("float32")
    if nodata is not None:
        mask_nodata = (red == nodata) | (nir == nodata)
    else:
        mask_nodata = np.zeros_like(red, dtype=bool)
    denom = nir + red
    denom[denom == 0] = np.nan
    ndvi = (nir - red) / denom
    ndvi[mask_nodata] = np.nan
    return ndvi


def gaussian_mascherato(arr: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Smoothing gaussiano che ignora correttamente i pixel non validi (nodata),
    usando una convoluzione pesata sulla maschera di validità invece di
    riempire con la media globale. Questo evita un artefatto molto comune:
    se i pixel invalidi (es. fuori dal buffer circolare ma dentro il bounding
    box quadrato del crop) venissero riempiti con la media e poi sfocati
    insieme ai dati veri, si creerebbe un gradiente artificiale lungo il
    bordo dell'area valida — e quel gradiente verrebbe scambiato per
    un'anomalia reale (falsi positivi sistematici sul perimetro)."""
    arr0 = np.where(valid, arr, 0.0).astype("float64")
    peso = valid.astype("float64")
    arr0_smooth = gaussian(arr0, sigma=sigma, preserve_range=True)
    peso_smooth = gaussian(peso, sigma=sigma, preserve_range=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        risultato = arr0_smooth / peso_smooth
    # dove il peso è troppo vicino a zero (quasi tutti i vicini invalidi)
    # il risultato non è affidabile: lo marchiamo come invalido a valle
    risultato[peso_smooth < 1e-6] = 0.0
    return risultato


def zscore_locale(arr: np.ndarray, sigma_smooth: float) -> np.ndarray:
    """Calcola uno z-score rispetto alla media/std globali dopo leggero smoothing
    mascherato, per enfatizzare scostamenti locali rispetto al pattern generale
    del campo senza introdurre artefatti sui bordi dell'area valida."""
    valid = np.isfinite(arr)
    if valid.sum() < 10:
        return np.zeros_like(arr)
    arr_smooth = gaussian_mascherato(arr, valid, sigma_smooth)
    media = np.mean(arr_smooth[valid])
    std = np.std(arr_smooth[valid])
    if std == 0:
        return np.zeros_like(arr)
    z = (arr_smooth - media) / std
    z[~valid] = 0.0
    return z


def residuo_dtm(dtm: np.ndarray, sigma_trend: float, nodata=None) -> np.ndarray:
    """Sottrae al DTM una superficie di tendenza a bassa frequenza (gaussian
    blur mascherato ad ampio raggio), isolando microrilievi locali (possibili
    strutture sepolte: fossati, muri, terrapieni), senza artefatti sui bordi
    dell'area valida (vedi gaussian_mascherato)."""
    valid = np.isfinite(dtm)
    if nodata is not None:
        valid &= dtm != nodata
    if valid.sum() < 10:
        return np.zeros_like(dtm)
    trend = gaussian_mascherato(dtm, valid, sigma_trend)
    residuo = np.where(valid, dtm - trend, 0.0)
    return residuo


def estrai_poligoni_da_maschera(
    maschera: np.ndarray,
    transform,
    crs,
    valori_score: np.ndarray,
    tipo: str,
    min_area_mq: float,
    eccentricita_min: float,
    solidity_max: float,
    pixel_area_mq: float,
) -> gpd.GeoDataFrame:
    """Etichetta la maschera binaria, filtra per geometria (forma allungata o
    squadrata, non macchie tonde casuali) e converte in poligoni georeferenziati."""
    etichette = label(maschera, connectivity=2)
    righe = []

    for regione in regionprops(etichette, intensity_image=valori_score):
        area_mq = regione.area * pixel_area_mq
        if area_mq < min_area_mq:
            continue

        eccentricita = regione.eccentricity
        solidity = regione.solidity

        # Teniamo forme allungate (fossati, linee) O con bassa "solidity"
        # (contorni irregolari/angolosi, tipici di perimetri strutturali)
        # scartando macchie tonde e piene (probabile rumore/vegetazione naturale)
        forma_interessante = (eccentricita >= eccentricita_min) or (solidity <= solidity_max)
        if not forma_interessante:
            continue

        maschera_regione = etichette == regione.label
        # connectivity=8 per coerenza con skimage.label(connectivity=2): senza
        # questo, una regione connessa solo in diagonale viene frammentata da
        # rio_shapes in molti poligoni separati (stesso bug già visto per i
        # bordi: qui serve un'unione esplicita dei frammenti in una singola
        # geometria per regione, altrimenti si duplica la riga per ogni
        # frammento con gli stessi identici attributi).
        frammenti = [
            shapely_shape(geom)
            for geom, _ in rio_shapes(
                maschera_regione.astype("uint8"),
                mask=maschera_regione,
                transform=transform,
                connectivity=8,
            )
        ]
        if not frammenti:
            continue
        geometria_unita = unary_union(frammenti) if len(frammenti) > 1 else frammenti[0]

        righe.append(
            {
                "tipo": tipo,
                "score_medio": float(np.nanmean(regione.image_intensity[regione.image])),
                "area_mq": round(area_mq, 2),
                "eccentricita": round(float(eccentricita), 3),
                "solidity": round(float(solidity), 3),
                "geometry": geometria_unita,
            }
            )

    if not righe:
        return gpd.GeoDataFrame(
            columns=["tipo", "score_medio", "area_mq", "eccentricita", "solidity", "geometry"],
            geometry="geometry",
            crs=crs,
        )

    return gpd.GeoDataFrame(righe, geometry="geometry", crs=crs)


# --------------------------------------------------------------------------
# Fusione multi-temporale (vegetazionale)
# --------------------------------------------------------------------------

def analizza_vegetazionale_multitemporale(
    ortofoto_date: list[tuple[str, str]],
    geom_buffer,
    crs_buffer,
    banda_red: int,
    banda_nir: int,
    params: ParametriAnalisi,
) -> gpd.GeoDataFrame:
    """Esegue il rilevamento NDVI indipendentemente su ciascuna ortofoto
    (una per data/stagione), poi incrocia i risultati: un segno che compare
    in più date è più affidabile di uno visto una sola volta (la vegetazione
    cambia nel corso dell'anno, quindi un'anomalia dovuta a una lavorazione
    agricola recente o a un fattore stagionale tende a non ripetersi identica
    su acquisizioni diverse, mentre una struttura sepolta stabile sì).

    Ritorna un GeoDataFrame con, in più rispetto al caso a singola data:
    - n_date: numero di date in cui il segno è stato rilevato
    - date_rilevazione: elenco (stringa, separata da virgola) delle date in
      cui è stato rilevato
    """
    gdf_per_data = []
    for path, data_str in ortofoto_date:
        print(f"       - acquisizione {data_str}: {path}")
        stack, profilo = ritaglia_raster(path, geom_buffer, crs_buffer)
        pixel_w = abs(profilo["transform"].a)
        pixel_h = abs(profilo["transform"].e)
        pixel_area_mq = pixel_w * pixel_h

        ndvi = calcola_ndvi(stack, banda_red, banda_nir, nodata=profilo.get("nodata"))
        z = zscore_locale(ndvi, params.ndvi_sigma_smooth)
        maschera = np.abs(z) >= params.ndvi_zscore_soglia

        gdf_data = estrai_poligoni_da_maschera(
            maschera,
            profilo["transform"],
            profilo["crs"] if profilo["crs"] else crs_buffer,
            z,
            tipo="vegetazionale",
            min_area_mq=params.ndvi_min_area_mq,
            eccentricita_min=params.eccentricita_min,
            solidity_max=params.solidity_max,
            pixel_area_mq=pixel_area_mq,
        )
        gdf_data["data_acquisizione"] = data_str
        print(f"         -> {len(gdf_data)} anomalie candidate in questa data")
        if len(gdf_data) > 100:
            print(f"         ATTENZIONE: numero di candidati insolitamente alto per questa "
                  f"acquisizione. Probabile rumore eccessivo nell'immagine (nuvole, ombre, "
                  f"qualità del dato) piuttosto che segni reali: verifica la qualità di "
                  f"'{path}' prima di fidarti dei risultati su questa data.")
        if not gdf_data.empty:
            gdf_per_data.append(gdf_data)

    colonne_vuote = [
        "tipo", "score_medio", "area_mq", "eccentricita", "solidity",
        "geometry", "n_date", "date_rilevazione",
    ]
    if not gdf_per_data:
        return gpd.GeoDataFrame(columns=colonne_vuote, geometry="geometry", crs=crs_buffer)

    gdf_tutte = gpd.GeoDataFrame(
        __import__("pandas").concat(gdf_per_data, ignore_index=True), crs=crs_buffer
    )

    # Clustering spaziale: un piccolo buffer (per colmare lievi scostamenti
    # di pochi pixel fra una data e l'altra dovuti a diversa risoluzione o
    # orientamento del raster) seguito da un'unione individua i gruppi di
    # poligoni, su date diverse, che rappresentano lo STESSO segno fisico.
    buffer_fusione_m = 3.0
    geometrie_bufferizzate = gdf_tutte.geometry.buffer(buffer_fusione_m)
    unione = unary_union(list(geometrie_bufferizzate))
    cluster_geoms = list(unione.geoms) if unione.geom_type == "MultiPolygon" else [unione]

    righe = []
    for cluster in cluster_geoms:
        appartengono = gdf_tutte[gdf_tutte.geometry.intersects(cluster)]
        if appartengono.empty:
            continue

        date_rilevate = sorted(appartengono["data_acquisizione"].unique().tolist())
        indice_max = appartengono["score_medio"].abs().idxmax()
        riga_forte = appartengono.loc[indice_max]

        geometria_originale_unita = unary_union(list(appartengono.geometry))

        righe.append(
            {
                "tipo": "vegetazionale",
                "score_medio": float(riga_forte["score_medio"]),
                "area_mq": round(float(geometria_originale_unita.area), 2),
                "eccentricita": float(riga_forte["eccentricita"]),
                "solidity": float(riga_forte["solidity"]),
                "geometry": geometria_originale_unita,
                "n_date": len(date_rilevate),
                "date_rilevazione": ",".join(date_rilevate),
                "data_acquisizione": ",".join(date_rilevate),
            }
        )

    return gpd.GeoDataFrame(righe, geometry="geometry", crs=crs_buffer)


# --------------------------------------------------------------------------
# Pipeline principale
# --------------------------------------------------------------------------

def analizza(
    path_cantiere: str,
    ortofoto_date: list[tuple[str, str]] | None,
    path_dtm: str | None,
    path_output: str | None,
    params: ParametriAnalisi,
    banda_red: int = 1,
    banda_nir: int = 4,
    path_output_pdf: str | None = None,
    nome_area: str = "area di cantiere",
    filigrana: str | None = None,
):
    if not ortofoto_date and path_dtm is None:
        raise ValueError("Serve almeno un'ortofoto o un DTM per fare l'analisi.")
    if path_output is None and path_output_pdf is None:
        raise ValueError("Specifica almeno --output (dati georeferenziati) o --output-pdf (report).")

    print(f"[1/5] Lettura area di cantiere: {path_cantiere}")
    gdf_cantiere = leggi_area_cantiere(path_cantiere)

    print(f"[2/5] Creazione buffer di {params.buffer_m} m")
    gdf_buffer = crea_buffer(gdf_cantiere, params.buffer_m)
    geom_buffer = gdf_buffer.geometry.iloc[0]
    crs_buffer = gdf_buffer.crs

    risultati = []

    if ortofoto_date:
        n_date = len(ortofoto_date)
        print(f"[3/5] Analisi vegetazionale (NDVI) su {n_date} acquisizion{'e' if n_date == 1 else 'i'}:")
        gdf_veg = analizza_vegetazionale_multitemporale(
            ortofoto_date, geom_buffer, crs_buffer, banda_red, banda_nir, params
        )
        if n_date > 1 and len(gdf_veg) > 0:
            confermati = int((gdf_veg["n_date"] >= 2).sum())
            print(f"       -> {len(gdf_veg)} segni candidati dopo fusione multi-data "
                  f"({confermati} confermati su 2+ date, {len(gdf_veg) - confermati} su singola data — saranno rimossi)")
        else:
            print(f"       -> {len(gdf_veg)} anomalie vegetazionali candidate")
        risultati.append(gdf_veg)
    else:
        print("[3/5] Nessuna ortofoto fornita, salto analisi vegetazionale")

    if path_dtm is not None:
        print(f"[4/5] Analisi microrilievi (DTM) su: {path_dtm}")
        stack_dtm, profilo_dtm = ritaglia_raster(path_dtm, geom_buffer, crs_buffer)
        dtm = stack_dtm[0].astype("float32")
        pixel_w = abs(profilo_dtm["transform"].a)
        pixel_h = abs(profilo_dtm["transform"].e)
        pixel_area_mq = pixel_w * pixel_h

        residuo = residuo_dtm(dtm, params.dtm_trend_sigma, nodata=profilo_dtm.get("nodata"))
        maschera_dtm = np.abs(residuo) >= params.dtm_residuo_soglia_m

        gdf_micro = estrai_poligoni_da_maschera(
            maschera_dtm,
            profilo_dtm["transform"],
            profilo_dtm["crs"] if profilo_dtm["crs"] else crs_buffer,
            residuo,
            tipo="microrilievo",
            min_area_mq=params.dtm_min_area_mq,
            eccentricita_min=params.eccentricita_min,
            solidity_max=params.solidity_max,
            pixel_area_mq=pixel_area_mq,
        )
        gdf_micro["n_date"] = 1
        gdf_micro["data_acquisizione"] = "DTM"
        gdf_micro["date_rilevazione"] = "DTM"
        print(f"       -> {len(gdf_micro)} anomalie di microrilievo candidate")
        risultati.append(gdf_micro)
    else:
        print("[4/5] Nessun DTM fornito, salto analisi microrilievi")

    gdf_cantiere_out = gdf_cantiere.to_crs(crs_buffer)

    risultati_non_vuoti = [r for r in risultati if not r.empty]
    if risultati_non_vuoti:
        crs_comune = risultati_non_vuoti[0].crs
        risultati_riproiettati = [r.to_crs(crs_comune) for r in risultati_non_vuoti]
        gdf_finale = gpd.GeoDataFrame(
            __import__("pandas").concat(risultati_riproiettati, ignore_index=True),
            crs=crs_comune,
        )
        gdf_finale = gdf_finale.to_crs(crs_buffer)
    else:
        gdf_finale = gpd.GeoDataFrame(
            columns=["tipo", "score_medio", "area_mq", "eccentricita", "solidity",
                     "n_date", "date_rilevazione", "data_acquisizione", "geometry"],
            geometry="geometry",
            crs=crs_buffer,
        )

    # Filtra: mantieni solo i segni confermati su almeno 2 date.
    if len(gdf_finale) > 0 and "n_date" in gdf_finale.columns:
        n_prima = len(gdf_finale)
        gdf_finale = gdf_finale[gdf_finale["n_date"] >= 2].reset_index(drop=True)
        if n_prima > len(gdf_finale):
            print(f"\nFiltro multi-data: {n_prima} segni → {len(gdf_finale)} confermati su 2+ date "
                  f"({n_prima - len(gdf_finale)} su singola data rimossi)")

    print(f"\nTotale segni rilevati: {len(gdf_finale)}")
    if len(gdf_finale) > 0:
        print(gdf_finale["tipo"].value_counts().to_string())

    passo = 5 if (path_output or path_output_pdf) else 4

    if path_output:
        print(f"\n[{passo-1}/{passo}] Scrittura dati georeferenziati (a pagamento): {path_output}")
        gdf_cantiere_out.to_file(path_output, layer="area_cantiere", driver="GPKG")
        gdf_buffer.to_file(path_output, layer="buffer_analisi", driver="GPKG")
        gdf_finale.to_file(path_output, layer="segni_rilevati", driver="GPKG")
        print(f"       Output: {path_output} (layer: area_cantiere, buffer_analisi, segni_rilevati)")

    if path_output_pdf:
        print(f"\n[{passo}/{passo}] Generazione report PDF (gratuito): {path_output_pdf}")
        from report_pdf import genera_report
        genera_report(
            path_output_pdf=path_output_pdf,
            gdf_segni=gdf_finale,
            gdf_cantiere=gdf_cantiere_out,
            buffer_m=params.buffer_m,
            nome_area=nome_area,
            ortofoto_date=ortofoto_date,
            path_dtm=path_dtm,
            filigrana=filigrana,
        )
        print(f"       Output: {path_output_pdf}")

    return gdf_finale


def esegui_analisi_automatica(
    path_cantiere: str,
    buffer_m: float,
    num_acquisizioni: int,
    path_output: str | None,
    path_output_pdf: str | None,
    nome_area: str,
    banda_red: int,
    banda_nir: int,
    qualita: str,
    filigrana: str | None,
    auto_tmp_dir: str = "auto_download_tmp",
) -> gpd.GeoDataFrame:
    """Pipeline --auto richiamabile in-process (import diretto), senza
    passare da subprocess + interprete Python esterno. Usata da
    server_locale.py quando e' compilato insieme a questo modulo in un
    unico eseguibile PyInstaller: senza questa funzione, rileva_segni.py
    dovrebbe comunque essere distribuito come file .py in chiaro per
    poter essere lanciato come processo separato (vedi CLI/main sotto,
    che resta disponibile per l'uso da riga di comando / sviluppo)."""
    import uuid as _uuid

    params = ParametriAnalisi(buffer_m=buffer_m)
    date_richieste = genera_date_automatiche(num_acquisizioni)
    cartella_tmp_analisi = os.path.join(auto_tmp_dir, _uuid.uuid4().hex[:8])

    gdf_cantiere_auto = leggi_area_cantiere(path_cantiere)
    gdf_buffer_auto = crea_buffer(gdf_cantiere_auto, buffer_m)
    ortofoto_date, path_dtm = scarica_dati_automatici(
        gdf_buffer_auto.geometry.iloc[0], gdf_buffer_auto.crs,
        date_richieste, cartella_tmp_analisi, qualita=qualita,
    )

    return analizza(
        path_cantiere=path_cantiere,
        ortofoto_date=ortofoto_date,
        path_dtm=path_dtm,
        path_output=path_output,
        params=params,
        banda_red=banda_red,
        banda_nir=banda_nir,
        path_output_pdf=path_output_pdf,
        nome_area=nome_area,
        filigrana=filigrana,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Rileva anomalie compatibili con segni archeologici attorno a un'area di cantiere."
    )
    parser.add_argument("--cantiere", required=True, help="GeoPackage con il poligono dell'area di cantiere")
    parser.add_argument(
        "--ortofoto", nargs=2, action="append", default=None,
        metavar=("PATH", "DATA"),
        help="GeoTIFF multibanda (Red+NIR) e la sua data di acquisizione (es. 2026-03-15 o 'primavera-2026'). "
             "Ripeti il flag per più acquisizioni in stagioni diverse: più date aumentano l'affidabilità "
             "dei segni vegetazionali, perché un'anomalia che si ripete su più acquisizioni è più credibile "
             "di una vista una sola volta. Esempio: --ortofoto marzo.tif 2026-03-15 --ortofoto luglio.tif 2026-07-20",
    )
    parser.add_argument("--dtm", default=None, help="GeoTIFF DTM (singola banda, quote in metri) — non serve più di uno: il microrilievo non cambia con le stagioni")
    parser.add_argument(
        "--auto", action="store_true",
        help="Scarica automaticamente ortofoto e DTM invece di leggerli da file locali: "
             "Geoportale Emilia-Romagna (alta risoluzione, nessuna credenziale) se il buffer "
             "è coperto, altrimenti Sentinel-2 + Copernicus DEM (fonte_satellitare.py, "
             "grossolano, richiede COPERNICUS_CLIENT_ID/SECRET). Incompatibile con --ortofoto/--dtm.",
    )
    parser.add_argument(
        "--data", action="append", default=None,
        help="Data (YYYY-MM-DD) da cercare per l'acquisizione Sentinel-2 in modalità --auto, "
             "se si ricade sulla modalità satellitare. Ripetibile per più date. Ignorato se "
             "il buffer è coperto dal Geoportale ER (ortofoto AGEA2020 ha una sola data fissa). "
             "Alternativa a --num-acquisizioni (non usarli insieme).",
    )
    parser.add_argument(
        "--num-acquisizioni", type=int, default=None, choices=range(2, 6), metavar="[2-5]",
        help="Quante acquisizioni stagionali usare per l'analisi multi-temporale (2-5): le date "
             "vengono scelte automaticamente, distribuite nell'ultimo anno (genera_date_automatiche). "
             "Alternativa più semplice a --data: l'utente sceglie solo QUANTE carte, non quando.",
    )
    parser.add_argument(
        "--auto-tmp-dir", default="auto_download_tmp",
        help="Cartella dove salvare i GeoTIFF scaricati in modalità --auto (default: ./auto_download_tmp)",
    )
    parser.add_argument(
        "--qualita", choices=["standard", "alta"], default="standard",
        help="In modalità --auto, se si ricade su Sentinel-2 (fuori copertura Geoportale ER o "
             "analisi multi-temporale): 'standard' usa Red+NIR a 10 m/pixel (default, veloce, "
             "nessuna dipendenza aggiuntiva); 'alta' applica super-risoluzione IA (SEN2SRLite) "
             "portando la risoluzione a ~2.5 m/pixel, ma scarica più dati da Copernicus ed è "
             "molto più lenta su CPU — richiede le librerie installate separatamente (vedi "
             "super_res.py). Se non disponibili, ricade automaticamente su 'standard' con un "
             "avviso, senza interrompere l'analisi. Ignorato se il Geoportale ER copre l'area "
             "(già alta risoluzione nativa, la super-risoluzione non serve).",
    )
    parser.add_argument("--buffer-m", type=float, default=500.0, help="Raggio del buffer in metri (default: 500)")
    parser.add_argument("--banda-red", type=int, default=1, help="Indice banda Red nell'ortofoto (1-based, default: 1)")
    parser.add_argument("--banda-nir", type=int, default=4, help="Indice banda NIR nell'ortofoto (1-based, default: 4)")
    parser.add_argument("--output", default=None, help="Percorso del GeoPackage di output (dati georeferenziati, livello a pagamento)")
    parser.add_argument("--output-pdf", default=None, help="Percorso del report PDF di output (livello gratuito)")
    parser.add_argument("--nome-area", default="area di cantiere", help="Nome dell'area da mostrare nel report PDF")
    parser.add_argument("--filigrana", default=None, help="Testo da stampare in filigrana su ogni pagina del PDF (piano Free)")
    parser.add_argument(
        "--output-riepilogo", default=None,
        help="Percorso di un file JSON dove scrivere il riepilogo strutturato "
             "dell'analisi (n_segni_totali, n_segni_multidata, date, qualita). "
             "Usato da server_locale.py per leggere i conteggi in modo robusto "
             "invece di fare grep sullo stdout.",
    )

    # Parametri avanzati opzionali
    parser.add_argument("--ndvi-soglia-z", type=float, default=1.8, help="Soglia z-score NDVI (default: 1.8)")
    parser.add_argument("--dtm-soglia-residuo-m", type=float, default=0.08, help="Soglia residuo DTM in metri (default: 0.08)")

    args = parser.parse_args()

    params = ParametriAnalisi(
        buffer_m=args.buffer_m,
        ndvi_zscore_soglia=args.ndvi_soglia_z,
        dtm_residuo_soglia_m=args.dtm_soglia_residuo_m,
    )

    if args.auto and (args.ortofoto or args.dtm):
        print("\nERRORE: --auto è incompatibile con --ortofoto/--dtm (scegli una modalità).", file=sys.stderr)
        sys.exit(1)

    MASSIMO_STAGIONI = _MASSIMO_STAGIONI_IMPORT  # unica sorgente: fonte_satellitare.py
    if args.ortofoto and len(args.ortofoto) > MASSIMO_STAGIONI:
        print(f"\nERRORE: massimo {MASSIMO_STAGIONI} acquisizioni (--ortofoto), {len(args.ortofoto)} richieste.", file=sys.stderr)
        sys.exit(1)
    if args.data and len(args.data) > MASSIMO_STAGIONI:
        print(f"\nERRORE: massimo {MASSIMO_STAGIONI} date (--data), {len(args.data)} richieste.", file=sys.stderr)
        sys.exit(1)
    if args.data and args.num_acquisizioni:
        print("\nERRORE: --data e --num-acquisizioni sono alternativi, usa solo uno dei due.", file=sys.stderr)
        sys.exit(1)

    date_richieste = args.data or []
    if args.num_acquisizioni:
        date_richieste = genera_date_automatiche(args.num_acquisizioni)
        print(f"[0/5] --num-acquisizioni {args.num_acquisizioni}: date generate automaticamente -> {', '.join(date_richieste)}")

    ortofoto_date = [(path, data) for path, data in args.ortofoto] if args.ortofoto else None
    path_dtm = args.dtm

    if args.auto:
        # Ogni analisi ottiene una sotto-cartella con UUID per evitare
        # collisioni sui nomi file fissi (es. auto_sentinel2_10b_2026-07-15.tif)
        # quando due analisi girano in parallelo con le stesse date richieste.
        import uuid as _uuid
        cartella_tmp_analisi = os.path.join(
            args.auto_tmp_dir, _uuid.uuid4().hex[:8]
        )
        print(f"[0/5] Download automatico in corso (--auto) -> {cartella_tmp_analisi}")
        gdf_cantiere_auto = leggi_area_cantiere(args.cantiere)
        gdf_buffer_auto = crea_buffer(gdf_cantiere_auto, args.buffer_m)
        ortofoto_date, path_dtm = scarica_dati_automatici(
            gdf_buffer_auto.geometry.iloc[0], gdf_buffer_auto.crs,
            date_richieste, cartella_tmp_analisi, qualita=args.qualita,
        )

    try:
        gdf_risultato = analizza(
            path_cantiere=args.cantiere,
            ortofoto_date=ortofoto_date,
            path_dtm=path_dtm,
            path_output=args.output,
            params=params,
            banda_red=args.banda_red,
            banda_nir=args.banda_nir,
            path_output_pdf=args.output_pdf,
            nome_area=args.nome_area,
            filigrana=args.filigrana,
        )
        # Bug 3: scrivi riepilogo strutturato in JSON per server_locale.py.
        # Più robusto del grep sullo stdout: il formato di questo file è un
        # contratto esplicito, non dipende dal testo dei messaggi di log.
        if args.output_riepilogo and gdf_risultato is not None:
            import json as _json
            from datetime import datetime as _dt
            n_totali = len(gdf_risultato)
            n_multidata = int((gdf_risultato.get("n_date", 1) >= 2).sum()) if n_totali > 0 else 0
            riepilogo = {
                "n_segni_totali": n_totali,
                "n_segni_multidata": n_multidata,
                "qualita": args.qualita,
                "date_usate": [d for _, d in ortofoto_date] if ortofoto_date else [],
            }
            with open(args.output_riepilogo, "w", encoding="utf-8") as f:
                _json.dump(riepilogo, f, ensure_ascii=False, indent=2)

            # Bug 6: marcatore di completamento nella cartella dell'analisi.
            # Permette di distinguere una cartella completa da una lasciata a
            # metà da un'analisi interrotta (timeout, errore, OOM).
            # Scriviamo done.json nella stessa cartella del riepilogo.
            cartella_riepilogo = os.path.dirname(args.output_riepilogo)
            if cartella_riepilogo:
                done_path = os.path.join(cartella_riepilogo, "done.json")
                with open(done_path, "w", encoding="utf-8") as f:
                    _json.dump({
                        "completato": True,
                        "timestamp": _dt.now().isoformat(timespec="seconds"),
                        "n_segni_totali": n_totali,
                    }, f, ensure_ascii=False)
    except Exception as e:
        print(f"\nERRORE: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
