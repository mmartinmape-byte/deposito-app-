@echo off
title Deposito - Gestion de Stock
echo.
echo  =========================================
echo   Deposito - Gestion de Stock por Palets
echo  =========================================
echo.

:: Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python no esta instalado en tu PC.
    echo.
    echo  Descargalo desde: https://www.python.org/downloads/
    echo  Al instalar, marca la casilla "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo  Verificando dependencias...
python -m pip install flask --quiet --disable-pip-version-check
echo  Listo.
echo.
echo  =========================================
echo   Abriendo en: http://localhost:5000
echo   Para cerrar la app, cerrá esta ventana.
echo  =========================================
echo.

:: Abrir navegador despues de 2 segundos
start "" cmd /c "timeout /t 2 >nul && start http://localhost:5000"

:: Iniciar Flask
python app.py
pause
