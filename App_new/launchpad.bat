@echo off
title Boveda - Launchpad
cd /d "%~dp0"
echo ========================================
echo   BOVEDA - Launchpad
echo   %CD%
echo ========================================
echo.

:: Crear config.json si no existe
if not exist config.json (
    echo {}>config.json
    echo Configuracion inicial creada.
)

:: Detectar Python automaticamente
setlocal
set SYS_PY=
where python.exe >nul 2>nul
if not errorlevel 1 set SYS_PY=python.exe
if "%SYS_PY%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 set SYS_PY=py -3
)
if "%SYS_PY%"=="" (
    echo [91mERROR: No se encontro Python en el sistema.[0m
    echo.
    echo Asegurate de tener Python instalado:
    echo   https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: Mostrar la ruta exacta del ejecutable para evitar conflictos de entorno
echo [96mEjecutable de sistema detectado en:[0m
where %SYS_PY%
echo.

:: Validar que Python sea de 64 bits (Requisito estricto de PySide6)
%SYS_PY% -c "import struct, sys; sys.exit(0 if struct.calcsize('P') * 8 == 64 else 1)"
if errorlevel 1 (
    echo [91mERROR: Se detecto una version de Python de 32 bits.[0m
    echo PySide6 y QtWebEngine requieren Python de 64 bits. 
    echo Descarga e instala la version "Windows installer (64-bit)" desde python.org.
    echo.
    pause
    exit /b 1
)

echo Usando Python de sistema (64-bit): %SYS_PY%

:: Crear entorno virtual (.venv) si no existe
if not exist .venv (
    echo  [93mCreando entorno virtual venv... [0m
    %SYS_PY% -m venv .venv
    if errorlevel 1 (
        echo  [91mERROR: No se pudo crear el entorno virtual. [0m
        pause
        exit /b 1
    )
    echo Entorno virtual creado exitosamente.
)

:: Definir PY_CMD usando el entorno virtual
set PY_CMD=.venv\Scripts\python.exe

:: Verificar e instalar dependencias dentro del venv
echo Verificando dependencias en el venv...
%PY_CMD% -c "import PySide6.QtWebEngineWidgets, googleapiclient, google_auth_oauthlib, github, requests" 2>nul
if errorlevel 1 (
    echo.
    echo [93mInstalando dependencias en el venv... esto puede tomar varios minutos.[0m
    echo.
    
    :: Actualizar pip primero
    %PY_CMD% -m pip install --upgrade pip
    
    %PY_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [91mERROR: No se pudieron instalar las dependencias en el venv.[0m
        echo.
        echo Intenta manualmente:
        echo   .venv\Scripts\pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo Dependencias instaladas correctamente.
)

echo.
echo Iniciando interfaz grafica desde el venv...
%PY_CMD% launchpad.py
if errorlevel 1 (
    echo.
    echo [91mERROR: No se pudo iniciar Launchpad.[0m
    echo.
    echo Asegurate de tener las dependencias instaladas:
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
endlocal