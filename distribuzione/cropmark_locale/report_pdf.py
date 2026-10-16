#!/usr/bin/env python3
"""
report_pdf.py
=============
Genera il report PDF "gratuito" per rileva_segni.py: una pagina di copertina
+ una scheda per ogni segno rilevato (mappa ravvicinata con il segno
evidenziato sopra ortofoto/DTM, tipo, area approssimativa, distanza e
direzione dal cantiere). Niente coordinate esatte: quelle restano riservate
al GeoPackage/GeoJSON a pagamento.

Se non viene rilevato nessun segno, produce comunque un PDF a una pagina
che attesta l'esito negativo dell'analisi.
"""

from __future__ import annotations

import io
import math
import os
import sys
import textwrap
import time
from datetime import datetime

import numpy as np
import geopandas as gpd
import rasterio
import requests
from PIL import Image
from rasterio.windows import from_bounds
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrow
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe


DIREZIONI_IT = [
    "Nord", "Nord-Est", "Est", "Sud-Est",
    "Sud", "Sud-Ovest", "Ovest", "Nord-Ovest",
]


# ---------------------------------------------------------------------------
# Palette e tipografia — ispirate ai report di survey aerofotografico
# (inchiostro scuro, ocra/bruno da mappe topografiche, verde musco per gli
# esiti confermati, rosso mattone per le anomalie): niente colori "da SaaS",
# niente beige/terracotta da template AI generico. Serif per i titoli
# (richiama la tipografia dei report di scavo a stampa), sans per corpo e
# didascalie: due famiglie, ruoli distinti, entrambe incluse di default in
# matplotlib (nessun font esterno da installare sulle macchine degli utenti).
# ---------------------------------------------------------------------------
INCHIOSTRO = "#232B33"       # testo principale, strutture
OCRA = "#8A6B3D"              # accenti neutri (scalebar, freccia nord, rule)
MUSCHIO = "#3F6B52"           # esito positivo, persistenza confermata
MATTONE = "#A23B2E"           # anomalia evidenziata, esito negativo del cantiere
GRIGIO_FREDDO = "#64707A"     # testo secondario
LINEA_CHIARA = "#CDD3D8"      # hairline divisorie

plt.rcParams["font.family"] = "sans-serif"
FONT_TITOLO = {"family": "serif"}

def _percorso_risorsa(nome_file: str) -> str:
    """Risolve il percorso di una risorsa bundled (PyInstaller) o locale."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome_file)


def _riga_divisoria(ax, y, x0=0.0, x1=1.0, colore=LINEA_CHIARA, linewidth=0.8):
    ax.plot([x0, x1], [y, y], color=colore, linewidth=linewidth,
            transform=ax.transAxes, clip_on=False)


def bearing_a_direzione(dx: float, dy: float) -> str:
    """Converte uno spostamento (dx, dy) in un CRS proiettato (est, nord) in
    una delle 8 direzioni cardinali in italiano."""
    angolo = math.degrees(math.atan2(dx, dy)) % 360  # 0 = Nord, orario
    indice = round(angolo / 45) % 8
    return DIREZIONI_IT[indice]


def arrotonda_area(area_mq: float) -> str:
    """Arrotonda l'area a una cifra significativa 'onesta' per un dato
    approssimativo (non vogliamo dare l'impressione di precisione che
    lo strumento non ha)."""
    if area_mq < 10:
        return f"~{round(area_mq)} mq"
    elif area_mq < 100:
        return f"~{round(area_mq / 5) * 5} mq"
    elif area_mq < 1000:
        return f"~{round(area_mq / 50) * 50} mq"
    else:
        return f"~{round(area_mq / 500) * 500} mq"


def arrotonda_distanza(dist_m: float) -> str:
    if dist_m < 50:
        return f"~{round(dist_m / 5) * 5} m"
    else:
        return f"~{round(dist_m / 10) * 10} m"


def calcola_distanza_direzione(geom_segno, geom_cantiere) -> tuple[str, str]:
    """Distanza minima dal perimetro del cantiere (non dal centroide: è la
    distanza che conta per capire se un segno è 'vicino' al cantiere) e
    direzione dal centroide del cantiere verso il centroide del segno."""
    distanza = geom_segno.distance(geom_cantiere)
    centro_cantiere = geom_cantiere.centroid
    centro_segno = geom_segno.centroid
    dx = centro_segno.x - centro_cantiere.x
    dy = centro_segno.y - centro_cantiere.y
    direzione = bearing_a_direzione(dx, dy)
    return arrotonda_distanza(distanza), direzione


def leggi_finestra_raster(path_raster: str, bounds, margine_m: float):
    """Legge una finestra del raster attorno a bounds (minx,miny,maxx,maxy)
    con un margine, restituendo l'array e l'extent in coordinate mappa per
    imshow. Ritorna None se bounds non interseca il raster."""
    minx, miny, maxx, maxy = bounds
    minx -= margine_m
    miny -= margine_m
    maxx += margine_m
    maxy += margine_m
    with rasterio.open(path_raster) as src:
        try:
            window = from_bounds(minx, miny, maxx, maxy, src.transform)
        except Exception:
            return None
        window = window.round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            return None
        arr = src.read(window=window, boundless=True, fill_value=src.nodata or 0)
        win_transform = src.window_transform(window)
        extent = (
            win_transform.c,
            win_transform.c + window.width * win_transform.a,
            win_transform.f + window.height * win_transform.e,
            win_transform.f,
        )
        return arr, extent, src.nodata


def prepara_sfondo(arr, nodata) -> np.ndarray:
    """Converte uno stack raster letto in qualcosa di mostrabile con imshow:
    RGB (prime 3 bande, stretch 2-98 percentile) o singola banda in scala
    di grigi (per il DTM, mostrato come hillshade semplice)."""
    bande = arr.shape[0]
    if bande >= 3:
        rgb = arr[:3].astype("float32")
        for i in range(3):
            banda = rgb[i]
            validi = banda[banda != (nodata or -99999)]
            if validi.size == 0:
                continue
            p2, p98 = np.percentile(validi, [2, 98])
            if p98 > p2:
                rgb[i] = np.clip((banda - p2) / (p98 - p2), 0, 1)
            else:
                rgb[i] = 0.5
        return np.transpose(rgb, (1, 2, 0))
    else:
        banda = arr[0].astype("float32")
        gy, gx = np.gradient(banda)
        shade = np.clip(1 - np.hypot(gx, gy) * 20, 0, 1)
        return shade


def disegna_scalebar(ax, extent):
    larghezza_m = extent[1] - extent[0]
    candidati = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
    lunghezza = min(candidati, key=lambda c: abs(c - larghezza_m / 4))
    x0 = extent[0] + larghezza_m * 0.05
    y0 = extent[2] + (extent[3] - extent[2]) * 0.06
    ax.plot([x0, x0 + lunghezza], [y0, y0], color=OCRA, linewidth=3,
            path_effects=[pe.withStroke(linewidth=5, foreground="white")])
    ax.text(x0 + lunghezza / 2, y0 + (extent[3] - extent[2]) * 0.015,
            f"{lunghezza} m", color=OCRA, ha="center", fontsize=8, fontweight="bold",
            path_effects=[pe.withStroke(linewidth=3, foreground="white")])


def disegna_freccia_nord(ax, extent):
    larghezza = extent[1] - extent[0]
    altezza = extent[3] - extent[2]
    x = extent[1] - larghezza * 0.08
    y0 = extent[2] + altezza * 0.08
    lunghezza_freccia = altezza * 0.08
    ax.add_patch(FancyArrow(
        x, y0, 0, lunghezza_freccia, width=larghezza * 0.004,
        head_width=larghezza * 0.018, head_length=lunghezza_freccia * 0.4,
        facecolor=OCRA, edgecolor="white", linewidth=0.5, length_includes_head=True,
    ))
    ax.text(x, y0 + lunghezza_freccia * 1.15, "N", color=OCRA, ha="center",
            fontsize=9, fontweight="bold",
            path_effects=[pe.withStroke(linewidth=3, foreground="white")])


def pagina_copertina(pdf: PdfPages, nome_area: str, n_segni: int, buffer_m: float,
                      params_info: dict):
    fig, ax = plt.subplots(figsize=(8.27, 11.69))  # A4 verticale
    ax.axis("off")

    oggi = datetime.now().strftime("%d/%m/%Y")
    esito = "Esito positivo" if n_segni > 0 else "Esito negativo"
    colore_esito = MATTONE if n_segni > 0 else MUSCHIO

    # Logo Matrice in alto a sinistra
    try:
        from matplotlib.image import imread as _imread
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
        _logo_path = _percorso_risorsa("icon-192.png")
        if os.path.exists(_logo_path):
            _logo_img = _imread(_logo_path)
            _oi = OffsetImage(_logo_img, zoom=0.12)
            _oi.image.axes = ax
            _ab = AnnotationBbox(_oi, (0.08, 0.955), xycoords='axes fraction',
                                 frameon=False, box_alignment=(0.5, 0.5))
            ax.add_artist(_ab)
    except Exception:
        pass
    ax.text(0.14, 0.955, "Matrice", ha="left", va="center", fontsize=11,
            fontweight="bold", color=INCHIOSTRO, transform=ax.transAxes)

    ax.text(0.5, 0.90, "Rilevamento segni archeologici", ha="center",
            fontsize=22, fontweight="bold", color=INCHIOSTRO,
            fontdict=FONT_TITOLO, transform=ax.transAxes)
    _riga_divisoria(ax, 0.875, x0=0.3, x1=0.7, colore=OCRA, linewidth=1.4)
    ax.text(0.5, 0.835, nome_area, ha="center", fontsize=13, color=INCHIOSTRO,
            transform=ax.transAxes)
    ax.text(0.5, 0.805, f"Analisi del {oggi}", ha="center", fontsize=9.5,
            color=GRIGIO_FREDDO, transform=ax.transAxes)

    ax.add_patch(plt.Rectangle((0.12, 0.615), 0.76, 0.115, transform=ax.transAxes,
                                facecolor=colore_esito, alpha=0.08,
                                edgecolor=colore_esito, linewidth=1.2))
    ax.plot([0.12, 0.12], [0.615, 0.73], color=colore_esito, linewidth=4,
            transform=ax.transAxes, clip_on=False, solid_capstyle="butt")
    ax.text(0.17, 0.695, esito, ha="left", fontsize=16, fontweight="bold",
            color=colore_esito, fontdict=FONT_TITOLO, transform=ax.transAxes)
    if n_segni > 0:
        ax.text(0.17, 0.655, f"{n_segni} segn{'o' if n_segni == 1 else 'i'} candidat{'o' if n_segni == 1 else 'i'} da validare sul campo",
                ha="left", fontsize=10.5, color=INCHIOSTRO, transform=ax.transAxes)
    else:
        ax.text(0.17, 0.655, "Nessuna anomalia compatibile rilevata nel buffer analizzato",
                ha="left", fontsize=10.5, color=INCHIOSTRO, transform=ax.transAxes)

    _riga_divisoria(ax, 0.565, colore=LINEA_CHIARA)

    campi = [
        ("Buffer analizzato", f"{buffer_m:.0f} m dal perimetro di cantiere"),
        ("Fonti dei dati", params_info.get("fonti", "n/d")),
    ]
    y = 0.525
    for etichetta, valore in campi:
        ax.text(0.1, y, etichetta.upper(), fontsize=7.5, color=OCRA,
                fontweight="bold", transform=ax.transAxes)
        righe = textwrap.wrap(valore, width=90)
        for k, riga in enumerate(righe):
            ax.text(0.1, y - 0.025 - k * 0.028, riga, fontsize=9.5,
                    color=INCHIOSTRO, transform=ax.transAxes)
        y -= 0.025 + len(righe) * 0.028 + 0.022

    _riga_divisoria(ax, y + 0.015, colore=LINEA_CHIARA)

    testo_metodo = (
        "Metodo — rilevamento automatico di anomalie vegetazionali (NDVI) e di\n"
        "microrilievo (residuo DTM), con filtro geometrico su forma (linearità,\n"
        "regolarità). Ogni segno è un candidato da validare da un operatore\n"
        "qualificato: non costituisce accertamento archeologico certo.\n\n"
        "Le coordinate esatte e i poligoni georeferenziati dei segni rilevati\n"
        "sono disponibili nel pacchetto dati a pagamento (GeoPackage/GeoJSON)."
    )
    ax.text(0.1, y - 0.03, testo_metodo, ha="left", va="top", fontsize=9,
            color=INCHIOSTRO, transform=ax.transAxes, wrap=True)

    _riga_divisoria(ax, 0.06, colore=LINEA_CHIARA)
    ax.text(0.1, 0.04, "Matrice è un progetto di Tommaso Saccone  ·  matrice.info@protonmail.com",
            ha="left", fontsize=7.5, color=GRIGIO_FREDDO, transform=ax.transAxes)
    ax.text(0.5, 0.015, "Documento generato automaticamente — per uso di supporto, non sostituisce la verifica sul campo.",
            ha="center", fontsize=7, color=GRIGIO_FREDDO, style="italic",
            transform=ax.transAxes)

    pdf.savefig(fig)
    plt.close(fig)


def disegna_pannello_mappa(ax, path_raster: str | None, bounds, margine: float,
                            geom, evidenzia: bool, mostra_scalebar: bool = False):
    """Disegna in ax lo sfondo raster ritagliato attorno a bounds, con il
    segno sovrapposto: contorno rosso pieno se evidenzia=True (anomalia
    rilevata su questa acquisizione), contorno grigio tratteggiato se
    evidenzia=False (acquisizione controllata ma segno non rilevato in
    quella data — utile per il confronto stagionale)."""
    sfondo = None
    extent = None
    if path_raster:
        risultato = leggi_finestra_raster(path_raster, bounds, margine)
        if risultato is not None:
            arr, extent, nodata = risultato
            sfondo = prepara_sfondo(arr, nodata)

    if sfondo is not None:
        cmap = None if sfondo.ndim == 3 else "gray"
        ax.imshow(sfondo, extent=extent, origin="upper", cmap=cmap)
    else:
        ax.set_facecolor("#dddddd")
        ax.text(0.5, 0.5, "(sfondo non\ndisponibile)", ha="center",
                 va="center", transform=ax.transAxes, color="gray", fontsize=8)
        extent = (bounds[0] - margine, bounds[2] + margine,
                  bounds[1] - margine, bounds[3] + margine)

    colore = MATTONE if evidenzia else "#999999"
    stile = "-" if evidenzia else "--"
    alpha_riempimento = 0.22 if evidenzia else 0.0

    poligoni = [geom] if geom.geom_type == "Polygon" else (
        list(geom.geoms) if geom.geom_type == "MultiPolygon" else [])
    for parte in poligoni:
        px, py = parte.exterior.xy
        ax.plot(px, py, color=colore, linewidth=2.2, linestyle=stile)
        if alpha_riempimento:
            ax.fill(px, py, color=colore, alpha=alpha_riempimento)

    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor(LINEA_CHIARA)
        spine.set_linewidth(0.8)

    if mostra_scalebar:
        disegna_scalebar(ax, extent)
        disegna_freccia_nord(ax, extent)


TILE_SIZE_PX = 256

# Cache tile satellite: LRU con tetto a 256 tile (~6 MB RAM con tile 256x256 RGB).
# Il dizionario illimitato precedente cresceva senza bound su report con molti
# segni distribuiti su aree diverse — ogni tile 256x256 RGB occupa ~192 KB.
from functools import lru_cache as _lru_cache

@_lru_cache(maxsize=256)
def _tile_cached(x: int, y: int, zoom: int) -> "Image.Image | None":
    """Scarica e memorizza un tile satellite con LRU bounded.
    Separato da _scarica_tile per mantenere la logica di retry leggibile."""
    return _scarica_tile_rete(x, y, zoom)


def _scarica_tile_rete(x: int, y: int, zoom: int) -> "Image.Image | None":
    """Scarica un tile satellite da Google con retry e log esplicito dei fallimenti."""
    url = f"https://mt1.google.com/vt/lyrs=s,h&x={x}&y={y}&z={zoom}"
    for tentativo in range(3):
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                print(
                    f"[report_pdf] ATTENZIONE: tile satellite ({x},{y},{zoom}) ha restituito "
                    f"Content-Type '{content_type}' invece di image/*. "
                    "L'endpoint Google potrebbe essere cambiato."
                )
                return None
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception as exc:
            if tentativo < 2:
                time.sleep(0.5 * (tentativo + 1))
            else:
                print(
                    f"[report_pdf] ATTENZIONE: tile satellite ({x},{y},{zoom}) non scaricato "
                    f"dopo 3 tentativi: {exc}. Il riquadro satellite sarà grigio."
                )
    return None


def _deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile2deg(x: int, y: int, zoom: int) -> tuple[float, float]:
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _scarica_tile(x: int, y: int, zoom: int) -> "Image.Image | None":
    """Punto di accesso pubblico: usa la LRU cache bounded."""
    return _tile_cached(x, y, zoom)


M_PER_DEG_LAT = 111320.0


def disegna_mappa_satellite(ax, lat: float, lon: float, scala_denominatore: int = 1000, zoom: int = 18):
    """Inset con vista satellite (tile Google) centrata sul segno, con un
    punto a indicarne la posizione, inquadrata alla scala di stampa
    richiesta (default 1:100, convenzione delle piante di cantiere): 1
    pollice sulla pagina = `scala_denominatore` pollici di terreno reale.
    L'estensione geografica mostrata si calcola dalla dimensione FISICA
    del riquadro sulla pagina (non da una frazione arbitraria del mosaico
    scaricato), così la scala dichiarata è quella vera indipendentemente
    da quanto è grande il riquadro nel layout."""
    fig = ax.figure
    fig_w_in, fig_h_in = fig.get_size_inches()
    bbox = ax.get_position()
    box_w_in = bbox.width * fig_w_in
    box_h_in = bbox.height * fig_h_in

    m_per_pollice = scala_denominatore * 0.0254
    largh_m = box_w_in * m_per_pollice
    alt_m = box_h_in * m_per_pollice
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    dlon = (largh_m / 2) / m_per_deg_lon
    dlat = (alt_m / 2) / M_PER_DEG_LAT

    xc, yc = _deg2tile(lat, lon, zoom)
    raggio_tile = 1
    n = raggio_tile * 2 + 1
    mosaico = Image.new("RGB", (TILE_SIZE_PX * n, TILE_SIZE_PX * n), "#dddddd")
    almeno_un_tile = False
    for dx in range(-raggio_tile, raggio_tile + 1):
        for dy in range(-raggio_tile, raggio_tile + 1):
            tile = _scarica_tile(xc + dx, yc + dy, zoom)
            if tile is not None:
                mosaico.paste(tile, ((dx + raggio_tile) * TILE_SIZE_PX, (dy + raggio_tile) * TILE_SIZE_PX))
                almeno_un_tile = True

    lat0, lon0 = _tile2deg(xc - raggio_tile, yc - raggio_tile, zoom)
    lat1, lon1 = _tile2deg(xc + raggio_tile + 1, yc + raggio_tile + 1, zoom)

    if almeno_un_tile:
        ax.imshow(np.array(mosaico), extent=(lon0, lon1, lat1, lat0), origin="upper")
    else:
        ax.set_facecolor("#dddddd")
        ax.text(0.5, 0.5, "(satellite\nnon disponibile)", ha="center", va="center",
                transform=ax.transAxes, color="gray", fontsize=7)

    ax.plot(lon, lat, marker="o", markersize=9, markerfacecolor=MATTONE,
            markeredgecolor="white", markeredgewidth=1.3, zorder=5)

    ax.set_xlim(lon - dlon, lon + dlon)
    ax.set_ylim(lat - dlat, lat + dlat)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor(LINEA_CHIARA)
        spine.set_linewidth(0.7)
    ax.text(0.5, 1.05, f"Vista satellite — scala 1:{scala_denominatore}", fontsize=6.5,
            ha="center", color=GRIGIO_FREDDO, transform=ax.transAxes)


def scheda_segno(pdf: PdfPages, indice: int, totale: int, riga, geom_cantiere,
                  ortofoto_date: list[tuple[str, str]], path_dtm: str | None,
                  crs_buffer=None):
    geom = riga.geometry
    bounds = geom.bounds
    larghezza = bounds[2] - bounds[0]
    altezza = bounds[3] - bounds[1]
    dimensione_max = max(larghezza, altezza)
    # Margine di contesto proporzionale ma limitato: per segni molto allungati
    # (es. una linea diagonale di centinaia di metri) un margine proporzionale
    # al lato più lungo farebbe "zoomare fuori" ben oltre la copertura reale
    # del raster, mostrando soprattutto nodata nero attorno a un piccolo
    # riquadro di dati veri. Il margine è quindi limitato a un tetto massimo.
    margine = max(20.0, min(dimensione_max * 0.25, 100.0))

    is_vegetazionale = riga["tipo"] == "vegetazionale"
    if is_vegetazionale:
        pannelli = list(ortofoto_date)  # una carta per ogni acquisizione disponibile
        date_rilevate = set(str(riga.get("date_rilevazione", "")).split(","))
    else:
        pannelli = [(path_dtm, "DTM")] if path_dtm else []
        date_rilevate = {"DTM"}

    n_pannelli = max(len(pannelli), 1)
    colonne = 2 if n_pannelli > 1 else 1
    righe_grid = math.ceil(n_pannelli / colonne)

    fig = plt.figure(figsize=(8.27, 11.69))
    altezza_fig_in = 11.69

    area_top, area_bottom = 0.90, 0.36
    area_left, area_width = 0.08, 0.84
    # Gap fisso abbastanza grande da contenere, tra due righe di pannelli,
    # sia l'etichetta sotto il pannello di sopra sia quella sopra il
    # pannello di sotto: con un gap proporzionale (frazione di panel_h) le
    # due etichette collidevano nelle griglie a 2 righe (3 acquisizioni),
    # perché la dimensione del font è assoluta in punti mentre panel_h si
    # restringe con più righe — lo scostamento percentuale non basta più.
    gap = 0.05
    panel_h = (area_top - area_bottom - gap * (righe_grid - 1)) / righe_grid
    panel_w = (area_width - gap * (colonne - 1)) / colonne

    ax_testo = fig.add_axes([0.08, 0.05, 0.60, 0.26])
    ax_testo.axis("off")

    if crs_buffer is not None:
        punto_wgs84 = gpd.GeoSeries([geom.centroid], crs=crs_buffer).to_crs(4326).iloc[0]
        ax_mappa = fig.add_axes([0.72, 0.065, 0.20, 0.20])
        disegna_mappa_satellite(ax_mappa, punto_wgs84.y, punto_wgs84.x)

    if not pannelli:
        ax_vuoto = fig.add_axes([area_left, area_bottom, area_width, area_top - area_bottom])
        ax_vuoto.axis("off")
        ax_vuoto.text(0.5, 0.5, "(nessuna carta disponibile)", ha="center", va="center", color="gray")
    else:
        header_offset_fig = 0.14 / altezza_fig_in
        footer_offset_fig = 0.08 / altezza_fig_in
        for k, (path_raster, data_str) in enumerate(pannelli):
            riga_k, col_k = divmod(k, colonne)
            x0 = area_left + col_k * (panel_w + gap)
            y0 = area_top - (riga_k + 1) * panel_h - riga_k * gap
            ax_pannello = fig.add_axes([x0, y0, panel_w, panel_h])
            x_centro = x0 + panel_w / 2

            rilevato_qui = data_str in date_rilevate
            disegna_pannello_mappa(
                ax_pannello, path_raster, bounds, margine, geom,
                evidenzia=rilevato_qui, mostra_scalebar=(k == 0),
            )

            # Etichette in coordinate FIGURA (non axes): uno scostamento
            # assoluto fisso, indipendente da quante righe ha la griglia —
            # vedi nota sul gap sopra.
            if is_vegetazionale:
                etichetta_data = data_str if data_str != "" else "data n/d"
                # Usa solo la data (YYYY-MM-DD) nell'header: la stringa completa
                # include la descrizione della fonte ed è troppo lunga per il gap.
                etichetta_breve = etichetta_data[:10] if len(etichetta_data) >= 10 else etichetta_data
                stato = "rilevato" if rilevato_qui else "non rilevato"
                colore_stato = MATTONE if rilevato_qui else GRIGIO_FREDDO
                fig.text(x_centro, y0 + panel_h + header_offset_fig, f"{etichetta_breve} — {stato}",
                         ha="center", va="bottom", fontsize=8, fontweight="bold", color=colore_stato)
            else:
                fig.text(x_centro, y0 + panel_h + header_offset_fig, "DTM (microrilievo, non stagionale)",
                         ha="center", va="bottom", fontsize=8, fontweight="bold", color=GRIGIO_FREDDO)

            fig.text(x_centro, y0 - footer_offset_fig, os.path.basename(path_raster) if path_raster else "n/d",
                     ha="center", va="top", fontsize=6.5, color=GRIGIO_FREDDO, style="italic")

    distanza_txt, direzione_txt = calcola_distanza_direzione(geom, geom_cantiere)
    tipo_label = "Anomalia vegetazionale (crop/soil mark)" if is_vegetazionale else "Microrilievo (shadow mark da DTM)"
    area_txt = arrotonda_area(riga["area_mq"])

    fig.text(0.08, 0.955, f"Segno {indice} di {totale}", fontsize=15, fontweight="bold",
             color=INCHIOSTRO, fontdict=FONT_TITOLO)
    fig.text(0.08, 0.925, tipo_label, fontsize=10.5, color=OCRA, fontweight="bold")
    fig.add_artist(plt.Line2D([0.08, 0.92], [0.915, 0.915], color=LINEA_CHIARA, linewidth=0.8))

    righe_info = [
        ("Area approssimativa", area_txt),
        ("Distanza dal cantiere", distanza_txt),
        ("Direzione dal cantiere", direzione_txt),
        ("Forma", "allungata/lineare" if riga["eccentricita"] >= 0.8 else
                  ("irregolare" if riga["solidity"] <= 0.7 else "compatta")),
    ]
    if is_vegetazionale:
        n_date_tot = len(ortofoto_date)
        n_date_rilevato = int(riga.get("n_date", len(date_rilevate)))
        righe_info.append((
            "Persistenza stagionale",
            f"rilevato in {n_date_rilevato} di {n_date_tot} acquisizion{'e' if n_date_tot == 1 else 'i'} verificate"
        ))

    y = 0.95
    for etichetta, valore in righe_info:
        ax_testo.text(0.0, y, etichetta.upper(), fontsize=8, fontweight="bold",
                      color=OCRA, transform=ax_testo.transAxes)
        ax_testo.text(0.45, y, valore, fontsize=10, color=INCHIOSTRO,
                      transform=ax_testo.transAxes)
        y -= 0.14

    _riga_divisoria(ax_testo, y + 0.05, colore=LINEA_CHIARA)
    ax_testo.text(0.0, 0.02,
                  "Candidato da validare sul campo. Coordinate esatte e poligono\n"
                  "georeferenziato disponibili nel pacchetto dati a pagamento.",
                  fontsize=8, color=GRIGIO_FREDDO, style="italic",
                  transform=ax_testo.transAxes)

    pdf.savefig(fig)
    plt.close(fig)


def pagina_chiusura(pdf: PdfPages, n_segni: int, n_omessi: int = 0):
    fig, ax = plt.subplots(figsize=(8.27, 11.69))
    ax.axis("off")

    ax.text(0.5, 0.62, f"Fine report — {n_segni} segn{'o' if n_segni == 1 else 'i'} candidat{'o' if n_segni == 1 else 'i'}",
            ha="center", fontsize=14, fontweight="bold", color=INCHIOSTRO,
            fontdict=FONT_TITOLO, transform=ax.transAxes)
    _riga_divisoria(ax, 0.60, x0=0.4, x1=0.6, colore=OCRA, linewidth=1.2)

    # Il box CTA sotto ha un'altezza fissa (3 righe di testo): quando c'è
    # anche la nota sui segni omessi (altre 3 righe sopra), il box va spinto
    # più in basso per non sovrapporsi — con una posizione fissa per
    # entrambi i blocchi, la nota finiva dentro il box (bug osservato).
    if n_omessi > 0:
        ax.text(0.5, 0.565,
                f"Mostrati i {n_segni - n_omessi} segni più significativi (persistenza su più date, poi punteggio).\n"
                f"Altri {n_omessi} segni di confidenza inferiore non sono inclusi in questo PDF ma sono\n"
                "presenti per intero nel pacchetto dati a pagamento.",
                ha="center", va="top", fontsize=8.5, color=GRIGIO_FREDDO, style="italic",
                transform=ax.transAxes)
        box_top = 0.47
    else:
        box_top = 0.55

    box_h = 0.09
    ax.add_patch(plt.Rectangle((0.1, box_top - box_h), 0.8, box_h, transform=ax.transAxes,
                                facecolor=OCRA, alpha=0.08, edgecolor=OCRA, linewidth=1.2))
    ax.text(0.5, box_top - box_h / 2,
            "Se sei interessato a scaricare i dati georeferenziati di ogni anomalia\n"
            "accedi a Matrice con piano Professionista o Aziendale.",
            ha="center", va="center", fontsize=10.5, fontweight="bold", color=INCHIOSTRO,
            transform=ax.transAxes)

    pdf.savefig(fig)
    plt.close(fig)


LIMITE_SEGNI_PDF_DEFAULT = 40


class _PdfPagesConFiligrana:
    """Wrapper attorno a PdfPages che timbra ogni pagina con una filigrana
    diagonale (piano Free: "versione di prova", richiesta 2026-10-07) prima
    di salvarla — stessa interfaccia di PdfPages (savefig/close), così
    pagina_copertina/scheda_segno/pagina_chiusura non cambiano: non sanno
    se stanno scrivendo su un PdfPages vero o su questo wrapper."""

    def __init__(self, path_output_pdf: str, testo: str | None):
        self._pdf = PdfPages(path_output_pdf)
        self._testo = testo

    def savefig(self, fig, *args, **kwargs):
        if self._testo:
            fig.text(0.5, 0.5, self._testo, fontsize=38, color="#999999", alpha=0.35,
                      ha="center", va="center", rotation=35, zorder=1000,
                      fontweight="bold")
        self._pdf.savefig(fig, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pdf.close()


def genera_report(
    path_output_pdf: str,
    gdf_segni: gpd.GeoDataFrame,
    gdf_cantiere: gpd.GeoDataFrame,
    buffer_m: float,
    nome_area: str = "area di cantiere",
    ortofoto_date: list[tuple[str, str]] | None = None,
    path_dtm: str | None = None,
    limite_segni_pdf: int = LIMITE_SEGNI_PDF_DEFAULT,
    filigrana: str | None = None,
):
    """
    limite_segni_pdf: tetto massimo di schede renderizzate nel PDF. Con molte
    date o raster rumorosi il numero di candidati può crescere molto (ogni
    segno vegetazionale genera un pannello per ogni acquisizione), rendendo
    il PDF enorme e lento da produrre. Oltre il tetto, il PDF mostra solo i
    segni più significativi (prima per persistenza su più date, poi per
    punteggio) e lo dichiara esplicitamente: l'elenco completo resta
    comunque nel GeoPackage/GeoJSON a pagamento, che non ha questo limite.
    """
    geom_cantiere = gdf_cantiere.union_all() if hasattr(gdf_cantiere, "union_all") else gdf_cantiere.unary_union
    n_segni_totali = len(gdf_segni)
    ortofoto_date = ortofoto_date or []

    fonti = []
    if ortofoto_date:
        n_date = len(ortofoto_date)
        date_elenco = ", ".join(data for _, data in ortofoto_date)
        fonti.append(f"ortofoto/NDVI su {n_date} acquisizion{'e' if n_date == 1 else 'i'} ({date_elenco})")
    if path_dtm:
        fonti.append("DTM LiDAR (microrilievo)")

    with _PdfPagesConFiligrana(path_output_pdf, filigrana) as pdf:
        pagina_copertina(pdf, nome_area, n_segni_totali, buffer_m, {"fonti": ", ".join(fonti) or "n/d"})

        if n_segni_totali > 0:
            gdf_ordinato = gdf_segni.copy()
            gdf_ordinato["abs_score"] = gdf_ordinato["score_medio"].abs()
            gdf_ordinato["n_date_ord"] = gdf_ordinato.get("n_date", 1)
            gdf_ordinato = gdf_ordinato.sort_values(
                ["n_date_ord", "abs_score"], ascending=[False, False]
            ).reset_index(drop=True)

            n_omessi = max(0, n_segni_totali - limite_segni_pdf)
            gdf_mostrati = gdf_ordinato.iloc[:limite_segni_pdf]

            for i, riga in gdf_mostrati.iterrows():
                scheda_segno(pdf, i + 1, len(gdf_mostrati), riga, geom_cantiere, ortofoto_date, path_dtm, crs_buffer=gdf_cantiere.crs)

            pagina_chiusura(pdf, n_segni_totali, n_omessi)

    return path_output_pdf
