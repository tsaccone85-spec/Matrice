@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Matrice - Pubblica plugin QGIS

echo ================================================================
echo   Matrice - Aggiorna e pubblica il plugin QGIS
echo ================================================================
echo.
echo Questo programma:
echo   1. Scarica le ultime modifiche dal repository del plugin (git pull).
echo   2. Verifica che core\supabase_config.py esista (va creato a mano
echo      UNA SOLA VOLTA, prima di usare questo script per la prima volta
echo      -- vedi README.md, sezione "Configurazione").
echo   3. Ricrea matrice_qgis_plugin.zip e lo copia nella cartella
echo      pubblica di download (app\plugin-qgis), pronto per
echo      "bash app\build.sh".
echo.
echo Va eseguito dalla cartella che CONTIENE matrice_qgis_plugin
echo (es. doppio clic dentro quella cartella), non da dentro di essa.
echo.
pause

if not exist "matrice_qgis_plugin" (
  echo.
  echo ERRORE: non trovo la cartella "matrice_qgis_plugin" qui dentro.
  echo Sposta questo file nella cartella che la contiene e riprova.
  echo.
  pause
  exit /b 1
)

echo.
echo --- 1/3: aggiornamento dal repository (git pull) ---
pushd matrice_qgis_plugin
git pull
if errorlevel 1 (
  echo.
  echo ATTENZIONE: "git pull" ha segnalato un problema qui sopra
  echo ^(es. modifiche locali non salvate^). Lo zip verra' comunque
  echo ricreato con quello che c'e' ora sul disco.
)
popd

echo.
echo --- 2/3: controllo core\supabase_config.py ---
if not exist "matrice_qgis_plugin\core\supabase_config.py" (
  echo.
  echo ERRORE: manca matrice_qgis_plugin\core\supabase_config.py
  echo ^(e' gitignored apposta, non arriva con il pull^).
  echo.
  echo Da fare UNA SOLA VOLTA, poi resta li' per sempre:
  echo   1. copy matrice_qgis_plugin\core\supabase_config.py.example ^
matrice_qgis_plugin\core\supabase_config.py
  echo   2. Apri il file appena creato e inserisci gli stessi valori
  echo      URL/KEY che trovi in app\js\store-supabase.js nel repo
  echo      dell'app web.
  echo   3. Rilancia questo script.
  echo.
  pause
  exit /b 1
)

echo OK, presente.

echo.
echo --- 3/3: ricreazione dello zip ---
if exist "matrice_qgis_plugin\core\__pycache__" (
  rmdir /s /q "matrice_qgis_plugin\core\__pycache__"
)
if exist "matrice_qgis_plugin.zip" del /q "matrice_qgis_plugin.zip"

powershell -NoProfile -Command "Compress-Archive -Path 'matrice_qgis_plugin' -DestinationPath 'matrice_qgis_plugin.zip' -Force"
if errorlevel 1 (
  echo.
  echo ERRORE durante la creazione dello zip.
  echo.
  pause
  exit /b 1
)

if not exist "app\plugin-qgis" (
  echo.
  echo ERRORE: non trovo la cartella "app\plugin-qgis" qui dentro
  echo ^(mi aspetto di essere lanciato da E:\Matrice\BD300926^).
  echo Lo zip e' pronto comunque qui: matrice_qgis_plugin.zip
  echo Spostalo a mano in app\plugin-qgis\matrice_qgis_plugin.zip
  echo.
  pause
  exit /b 1
)

move /y "matrice_qgis_plugin.zip" "app\plugin-qgis\matrice_qgis_plugin.zip" >nul

echo.
echo ================================================================
echo  FATTO! app\plugin-qgis\matrice_qgis_plugin.zip e' aggiornato.
echo ================================================================
echo.
echo ULTIMO PASSO, obbligatorio per pubblicarlo online:
echo     bash app\build.sh
echo.
echo Senza questo, il link di download sul sito resta quello vecchio
echo anche se il file sul disco e' gia' aggiornato.
echo.
pause
