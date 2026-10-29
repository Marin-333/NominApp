"""
paths.py - Rutas persistentes de la aplicación.

PROBLEMA RAÍZ resuelto aquí:
  PyInstaller extrae los archivos del .exe a una carpeta temporal (_MEIPASS)
  que se BORRA cada vez que se cierra la aplicación. Si database.py usa
  __file__ para calcular DB_PATH, la base de datos queda dentro de _MEIPASS
  y se pierde al cerrar.

SOLUCIÓN:
  Usar sys.executable (ruta al .exe real) para guardar nomina.db y reports/
  junto al ejecutable, donde sí persisten entre sesiones.
"""
import os
import sys


def get_app_dir() -> str:
    """
    Carpeta donde persisten los datos de la aplicación.
    - .exe compilado  → carpeta que contiene NominaApp.exe
    - script Python   → carpeta que contiene app.py / database.py
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR     = get_app_dir()
DB_PATH     = os.path.join(APP_DIR, "nomina.db")
REPORTS_DIR = os.path.join(APP_DIR, "reports")
LOG_DIR     = os.path.join(APP_DIR, "logs")
BACKUP_DIR  = os.path.join(APP_DIR, "backups")

# Versión de la aplicación (se registra en la tabla meta de la BD)
APP_VERSION = "4.1.0"

# Crear carpetas persistentes si no existen
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)


def get_reports_dir(empresa_id=None):
    """Retorna la carpeta de reportes para una empresa específica.
    Si empresa_id es None, retorna la carpeta general (para superadmin)."""
    if empresa_id is not None:
        emp_dir = os.path.join(REPORTS_DIR, f"empresa_{empresa_id}")
        os.makedirs(emp_dir, exist_ok=True)
        return emp_dir
    return REPORTS_DIR
