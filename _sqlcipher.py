"""_sqlcipher.py - Precarga la DLL de SQLCipher antes de `import sqlite3`.

sqlite_cipher/sqlite3.dll es e_sqlcipher.dll (SQLCipher community, extraída
del paquete NuGet SQLitePCLRaw.lib.e_sqlcipher 2.1.11, Apache-2.0/BSD)
renombrada a sqlite3.dll. Al cargarla con ctypes antes de importar el módulo
sqlite3 estándar, _sqlite3.pyd resuelve la dependencia "sqlite3.dll" contra
el módulo ya cargado y PRAGMA key / sqlcipher_export quedan disponibles
sin cambiar el API de sqlite3.

Si la DLL no existe, CIFRADO_OK queda False y la app funciona en texto plano
(útil fuera de Windows o en entornos sin la DLL).
"""
import ctypes
import os
import sys

CIFRADO_OK = False

_dll = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
    "sqlite_cipher", "sqlite3.dll")

if os.path.exists(_dll):
    try:
        ctypes.CDLL(_dll)
        import sqlite3 as _s
        _c = _s.connect(":memory:")
        CIFRADO_OK = bool(
            _c.execute("PRAGMA cipher_version").fetchone())
        _c.close()
    except Exception:
        CIFRADO_OK = False
