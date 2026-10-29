@echo off
REM ============================================================
REM  build.bat - Script de compilación para NóminaApp
REM  Ejecutar en Windows con Python instalado
REM ============================================================

echo ============================================
echo   NominaApp - Script de Compilacion
echo ============================================
echo.

REM Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instala Python 3.10+ desde python.org
    pause
    exit /b 1
)

echo [1/4] Instalando dependencias...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR al instalar dependencias.
    pause
    exit /b 1
)

echo.
echo [2/4] Instalando PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo ERROR al instalar PyInstaller.
    pause
    exit /b 1
)

echo.
echo [3/4] Compilando aplicacion...
python -m PyInstaller NominaApp.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR durante la compilacion.
    pause
    exit /b 1
)

echo.
echo [4/4] Compilacion exitosa!
echo.
echo El ejecutable se encuentra en: dist\NominaApp.exe
echo.
echo NOTA: Distribuye el archivo NominaApp.exe junto con
echo       una carpeta 'reports' vacia para los PDFs generados.
echo.
pause
