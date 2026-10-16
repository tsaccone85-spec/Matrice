@echo off
rem Compila server_locale.exe (un unico eseguibile, tutto incluso: server,
rem pipeline di rilevamento, download satellite/geoportale, report PDF e
rem modulo di super-risoluzione IA opzionale) su Windows. Da lanciare in
rem questa cartella, con Python 3.11+ installato.
rem
rem Dopo la build, l'exe generato va in dist\server_locale.exe: copialo
rem accanto a avvia_server_locale_windows.bat e rizippa i due file per la
rem release GitHub. Nessun file .py va piu' distribuito in chiaro.

python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check ^
    pyinstaller numpy rasterio geopandas scikit-image scipy shapely ^
    matplotlib requests Pillow torch sen2sr mlstac
if errorlevel 1 (
  echo ERRORE durante l'installazione delle dipendenze.
  pause
  exit /b 1
)

rem --collect-all per i package con plugin/dati caricati dinamicamente
rem (driver GDAL/Fiona di rasterio e geopandas, risorse di matplotlib),
rem che PyInstaller non individua da solo seguendo gli import statici.
pyinstaller --onefile --name server_locale ^
    --collect-all rasterio ^
    --collect-all fiona ^
    --collect-all geopandas ^
    --collect-all pyproj ^
    --collect-all matplotlib ^
    --collect-all skimage ^
    --collect-all torch ^
    --collect-all sen2sr ^
    --collect-all mlstac ^
    --hidden-import rileva_segni ^
    --hidden-import fonte_satellitare ^
    --hidden-import fonte_geoportale_er ^
    --hidden-import report_pdf ^
    --hidden-import super_res ^
    server_locale.py

rem Se vuoi il logo Matrice in copertina del PDF, copia qui icon-192.png
rem prima della build e aggiungi: --add-data "icon-192.png;." (opzionale,
rem report_pdf.py ricade senza errori se il file non e' incluso).

if errorlevel 1 (
  echo ERRORE durante la build PyInstaller.
  pause
  exit /b 1
)

echo.
echo Fatto: dist\server_locale.exe
pause
