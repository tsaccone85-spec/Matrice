#!/usr/bin/env python3
"""
super_res.py
============
Super-risoluzione opzionale delle bande Sentinel-2 (10 m/pixel -> ~2.5
m/pixel) tramite SEN2SRLite (pacchetto "sen2sr", ESA OpenSR), usata solo
nella modalita "alta qualita" (--qualita alta) quando rileva_segni.py ricade
sulla modalita satellitare.

NON FA PARTE DELL'INSTALLAZIONE STANDARD: le dipendenze pesanti (torch,
sen2sr, mlstac) vengono importate solo quando questo modulo viene
effettivamente usato (import lazy), cosi' chi non seleziona la modalita
"alta qualita" non deve scaricarle.

ATTENZIONE: il tempo di inferenza su CPU per una singola scena non e' stato
misurato contro il servizio Copernicus reale (ambiente di sviluppo senza
accesso di rete, vedi fonte_satellitare.py). Su un buffer tipico (poche
centinaia di metri, quindi un'immagine piccola) dovrebbe restare nell'ordine
di secondi/pochi minuti su CPU, ma va verificato con un test reale (vedi
test_sen2sr_lite.py) prima di considerarla una stima affidabile.
"""

from __future__ import annotations

import numpy as np
import rasterio
import rasterio.transform

from fonte_satellitare import ORDINE_BANDE_10

# Indici (0-based) di Red e NIR dentro ORDINE_BANDE_10, per estrarre dopo la
# super-risoluzione solo le 2 bande che servono alla pipeline NDVI esistente.
_IDX_RED = ORDINE_BANDE_10.index("B04")
_IDX_NIR = ORDINE_BANDE_10.index("B08")


class SuperResNonDisponibile(Exception):
    """Sollevata quando le librerie di super-risoluzione non sono installate
    o il modello non puo' essere caricato. Chi chiama puo' intercettarla e
    ricadere sulla modalita standard."""


def _carica_modello(model_dir: str = "model/SEN2SRLite"):
    try:
        import torch
        import mlstac
    except ImportError as exc:
        raise SuperResNonDisponibile(
            "Librerie di super-risoluzione non installate (torch/mlstac/sen2sr). "
            "Esegui avvia_server_locale_windows.bat e scegli 'alta qualita' "
            "al prossimo avvio per installarle, oppure manualmente: "
            "pip install sen2sr mlstac torch"
        ) from exc

    from pathlib import Path
    path_modello = Path(model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if not path_modello.exists():
        print("       [alta qualita] Primo utilizzo: scarico il modello "
              "SEN2SRLite (richiede internet, qualche decina di MB)...")
        mlstac.download(
            file="https://huggingface.co/tacofoundation/sen2sr/resolve/main/SEN2SRLite/main/mlm.json",
            output_dir=str(path_modello),
        )

    modello = mlstac.load(str(path_modello)).compiled_model(device=device)
    return modello.to(device), device


def super_risolvi_a_red_nir(
    path_10_bande: str,
    path_output_red_nir: str,
    model_dir: str = "model/SEN2SRLite",
) -> str:
    """Legge il GeoTIFF a 10(+1) bande scaricato da
    fonte_satellitare.scarica_sentinel2_10bande, applica SEN2SRLite e scrive
    un GeoTIFF a 2 bande (Red, NIR) alla risoluzione super-risolta (~2.5
    m/pixel), georeferenziato correttamente. Il GeoTIFF risultante e'
    compatibile con la pipeline NDVI esistente (banda-red=1, banda-nir=2).

    Solleva SuperResNonDisponibile se le librerie non sono installate: chi
    chiama decide se ricadere sulla modalita standard (consigliato) o
    interrompere.
    """
    import torch
    import sen2sr

    modello, device = _carica_modello(model_dir)

    with rasterio.open(path_10_bande) as src:
        bande = src.read()[: len(ORDINE_BANDE_10)]  # scarta dataMask
        profilo = src.profile.copy()
        trasform_orig = src.transform
        crs = src.crs

    # NB: le bande arrivano gia' come riflettanza 0-1 (sampleType FLOAT32
    # nell'evalscript Sentinel Hub, non DN 0-10000): NON vanno divise di
    # nuovo. Un primo test (2026-10-09) con /10000 qui schiacciava l'input
    # quasi a zero, producendo un output del modello pressoche' uniforme.
    X_numpy = bande.astype("float32")

    # sen2sr.predict_large lavora a tile fisse di 128x128px (overlap=32,
    # stride=96): su immagini piu' piccole di una tile o con dimensioni non
    # multiple dello stride, lo stitching interno produce un errore di
    # dimensioni ("Expected size N but got size N+1") - bug riscontrato
    # testando su un'area di poche centinaia di metri (tipica di questo
    # strumento). Soluzione: facciamo noi il padding (reflect, solo in
    # basso/a destra) fino al prossimo multiplo valido PRIMA di passare i
    # dati a predict_large, poi ritagliamo il risultato alla sola area
    # originale (angolo in alto a sinistra, visto che il padding e' solo
    # sugli altri due lati).
    PATCH, STRIDE = 128, 96
    h_orig, w_orig = X_numpy.shape[-2], X_numpy.shape[-1]
    h_target = max(PATCH, h_orig if h_orig <= PATCH else PATCH + ((h_orig - PATCH + STRIDE - 1) // STRIDE) * STRIDE)
    w_target = max(PATCH, w_orig if w_orig <= PATCH else PATCH + ((w_orig - PATCH + STRIDE - 1) // STRIDE) * STRIDE)
    pad_h, pad_w = h_target - h_orig, w_target - w_orig
    if pad_h or pad_w:
        print(f"       [alta qualita] Immagine {w_orig}x{h_orig}px piu' piccola/non multipla "
              f"della tile ({PATCH}px): aggiungo padding a {w_target}x{h_target}px "
              f"(ritagliato automaticamente dal risultato).")
        X_numpy = np.pad(X_numpy, ((0, 0), (0, pad_h), (0, pad_w)), mode="reflect")

    X = torch.from_numpy(X_numpy).float().to(device)
    X = torch.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    print(f"       [alta qualita] Super-risoluzione in corso su {device} "
          f"(immagine {X.shape[-1]}x{X.shape[-2]} px a 10m, padding incluso)...")
    with torch.no_grad():
        superX = sen2sr.predict_large(model=modello, X=X, overlap=32)

    super_numpy = superX.detach().cpu().numpy()
    fattore_scala = super_numpy.shape[-1] / X_numpy.shape[-1]

    if pad_h or pad_w:
        h_crop = round(h_orig * fattore_scala)
        w_crop = round(w_orig * fattore_scala)
        super_numpy = super_numpy[:, :h_crop, :w_crop]

    # Riflettanza 0-1, stessa convenzione del percorso standard
    # (EVALSCRIPT_RED_NIR) - calcola_ndvi() e' scala-invariante quindi va
    # bene cosi', nessuna riconversione necessaria.
    #
    # BANDA 3 — maschera di copertura (dataMask), riportata alla risoluzione
    # super-risolta per portare il file a 3 bande come il percorso standard
    # (EVALSCRIPT_RED_NIR: red, nir, dataMask). Senza questa terza banda
    # prepara_sfondo() in report_pdf.py cade nel ramo "singola banda" e
    # visualizza lo sfondo come pseudo-hillshade invece di falso-colore.
    # La maschera originale e' nell'ultima banda del file a 10+1 bande
    # (indice len(ORDINE_BANDE_10) = 10, 0-based, ovvero la banda 11);
    # la riportiamo alla dimensione super-risolta con un resize nearest-
    # neighbor per non interpolare valori binari.
    from skimage.transform import resize as sk_resize

    maschera_orig = bande[len(ORDINE_BANDE_10)] if bande.shape[0] > len(ORDINE_BANDE_10) else np.ones(
        (h_orig, w_orig), dtype="float32"
    )
    h_sr, w_sr = super_numpy.shape[-2], super_numpy.shape[-1]
    maschera_sr = sk_resize(
        maschera_orig.astype("float32"),
        (h_sr, w_sr),
        order=0,           # nearest-neighbor: non interpola valori binari
        preserve_range=True,
        anti_aliasing=False,
    ).astype("float32")

    red_nir_mask = np.stack(
        [super_numpy[_IDX_RED], super_numpy[_IDX_NIR], maschera_sr],
        axis=0,
    ).astype("float32")

    # Nuovo transform: stessa origine, pixel piu' piccolo del fattore di
    # scala applicato dalla super-risoluzione (tipicamente 4x: 10m -> 2.5m).
    nuovo_transform = rasterio.Affine(
        trasform_orig.a / fattore_scala, trasform_orig.b, trasform_orig.c,
        trasform_orig.d, trasform_orig.e / fattore_scala, trasform_orig.f,
    )
    profilo.update(
        count=3,
        height=red_nir_mask.shape[1],
        width=red_nir_mask.shape[2],
        transform=nuovo_transform,
        crs=crs,
        dtype="float32",
    )
    with rasterio.open(path_output_red_nir, "w", **profilo) as dst:
        dst.write(red_nir_mask)

    print(f"       [alta qualita] Fatto: {red_nir_mask.shape[2]}x{red_nir_mask.shape[1]} px "
          f"(~{RISOLUZIONE_STIMATA_M(trasform_orig.a, fattore_scala):.1f} m/pixel)")
    return path_output_red_nir


def super_risolvi_tutte_le_bande(
    path_10_bande: str,
    path_output: str,
    model_dir: str = "model/SEN2SRLite",
) -> str:
    """
    Come super_risolvi_a_red_nir ma scrive tutte le 10 bande S2 (~2.5 m/px).
    Usato da scarica_carte.py per RGB, falso colore e NDVI ad alta qualità.
    Solleva SuperResNonDisponibile se torch/sen2sr non sono installati.
    """
    import torch
    import sen2sr

    modello, device = _carica_modello(model_dir)

    with rasterio.open(path_10_bande) as src:
        bande = src.read()[:len(ORDINE_BANDE_10)]
        profilo = src.profile.copy()
        trasform_orig = src.transform

    X_numpy = bande.astype("float32")

    PATCH, STRIDE = 128, 96
    h_orig, w_orig = X_numpy.shape[-2], X_numpy.shape[-1]
    h_target = max(PATCH, h_orig if h_orig <= PATCH else PATCH + ((h_orig - PATCH + STRIDE - 1) // STRIDE) * STRIDE)
    w_target = max(PATCH, w_orig if w_orig <= PATCH else PATCH + ((w_orig - PATCH + STRIDE - 1) // STRIDE) * STRIDE)
    pad_h, pad_w = h_target - h_orig, w_target - w_orig
    if pad_h or pad_w:
        print(f"       [alta qualita] Padding {w_orig}x{h_orig} -> {w_target}x{h_target}px")
        X_numpy = np.pad(X_numpy, ((0, 0), (0, pad_h), (0, pad_w)), mode="reflect")

    X = torch.from_numpy(X_numpy).float().to(device)
    X = torch.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    print(f"       [alta qualita] Super-risoluzione 10 bande su {device} "
          f"({X.shape[-1]}x{X.shape[-2]}px)...", flush=True)
    with torch.no_grad():
        superX = sen2sr.predict_large(model=modello, X=X, overlap=32)

    super_numpy = superX.detach().cpu().numpy()
    fattore_scala = super_numpy.shape[-1] / X_numpy.shape[-1]

    if pad_h or pad_w:
        h_crop = round(h_orig * fattore_scala)
        w_crop = round(w_orig * fattore_scala)
        super_numpy = super_numpy[:, :h_crop, :w_crop]

    nuovo_transform = rasterio.Affine(
        trasform_orig.a / fattore_scala, trasform_orig.b, trasform_orig.c,
        trasform_orig.d, trasform_orig.e / fattore_scala, trasform_orig.f,
    )
    profilo.update(
        count=len(ORDINE_BANDE_10),
        height=super_numpy.shape[1],
        width=super_numpy.shape[2],
        dtype="float32",
        transform=nuovo_transform,
        compress="deflate",
        predictor=3,
    )
    with rasterio.open(path_output, "w", **profilo) as dst:
        dst.write(super_numpy.astype("float32"))

    ris_m = RISOLUZIONE_STIMATA_M(trasform_orig.a, fattore_scala)
    print(f"       [alta qualita] Fatto: {super_numpy.shape[2]}x{super_numpy.shape[1]}px "
          f"(~{ris_m:.1f} m/px)", flush=True)
    return path_output


def RISOLUZIONE_STIMATA_M(pixel_size_originale: float, fattore_scala: float) -> float:
    # pixel_size_originale e' in gradi (CRS geografico) o metri, a seconda
    # della sorgente: qui serve solo una stima indicativa per il messaggio
    # di log, quindi assumiamo input tipico Sentinel-2 (10m) se in metri.
    return (abs(pixel_size_originale) * 111_000 / fattore_scala
            if abs(pixel_size_originale) < 1 else abs(pixel_size_originale) / fattore_scala)
