"""
database.py - Módulo de base de datos SQLite para el sistema de nómina
"""
import _sqlcipher  # precarga SQLCipher ANTES de importar sqlite3
import sqlite3
import hashlib
import os
import sys
import base64
import ctypes
import ctypes.wintypes
import shutil
import bcrypt
import urllib.request
import xml.etree.ElementTree as ET
import xml.dom.minidom
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from paths import DB_PATH, BACKUP_DIR, APP_VERSION, get_reports_dir

SCHEMA_VERSION = 1
MAX_AUTOBACKUPS = 10

# ─── Seguridad: contraseñas y secretos ────────────────────────────────────────
MAX_INTENTOS_LOGIN = 5      # intentos antes de bloquear la cuenta
MINUTOS_BLOQUEO    = 15     # duración del bloqueo
PASSWORD_FABRICA   = "Admin2024!"   # contraseña por defecto del superadmin


def politica_password_ok(pw: str):
    """Valida la política de contraseñas: mínimo 8 chars, letra + número."""
    if not pw:                    return False, "La contraseña no puede estar vacía."
    if len(pw) < 8:               return False, "Mínimo 8 caracteres."
    if not any(c.isalpha() for c in pw):
        return False, "Debe incluir al menos una letra."
    if not any(c.isdigit() for c in pw):
        return False, "Debe incluir al menos un número."
    return True, ""


# Cifrado de secretos en reposo con DPAPI (CryptProtectData) — la protección
# queda ligada al usuario de Windows actual; no hay llave que gestionar.
if sys.platform == "win32":
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi_crypt(data: bytes, protect: bool) -> bytes:
        blob_in = _DATA_BLOB(
            len(data),
            ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                        ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        fn = (ctypes.windll.crypt32.CryptProtectData if protect
              else ctypes.windll.crypt32.CryptUnprotectData)
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0,
                  ctypes.byref(blob_out)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(
                ctypes.cast(blob_out.pbData, ctypes.c_void_p))
else:
    def _dpapi_crypt(data: bytes, protect: bool) -> bytes:
        raise RuntimeError("DPAPI solo está disponible en Windows")

_DPAPI_PREFIX = "dpapi:"


def cifrar_secreto(texto: str) -> str:
    """Cifra un secreto para almacenarlo en BD. Idempotente (si ya viene
    cifrado lo devuelve igual)."""
    if not texto or texto.startswith(_DPAPI_PREFIX):
        return texto
    return _DPAPI_PREFIX + base64.b64encode(
        _dpapi_crypt(texto.encode("utf-8"), protect=True)).decode("ascii")


def descifrar_secreto(valor: str) -> str:
    """Descifra un secreto de BD. Valores en texto plano (legado) se
    devuelven tal cual."""
    if not valor or not valor.startswith(_DPAPI_PREFIX):
        return valor or ""
    return _dpapi_crypt(base64.b64decode(valor[len(_DPAPI_PREFIX):]),
                        protect=False).decode("utf-8")


# ─── Cifrado de la base de datos (Fase 2: SQLCipher + DPAPI) ──────────────────
# La llave raw de 32 bytes vive en <nomina.db>.key protegida con DPAPI (ligada
# al usuario de Windows). Nunca se embebe en el .exe ni se versiona.
CIFRADO_DB = _sqlcipher.CIFRADO_OK
_CLAVES_DB = {}


def _ruta_llave():
    return DB_PATH + ".key"


def _proteger_llave(raw: bytes) -> bytes:
    try:
        return b"DP1" + _dpapi_crypt(raw, protect=True)
    except Exception:
        return b"RAW" + raw   # fallback fuera de Windows


def _desproteger_llave(blob: bytes) -> bytes:
    if blob.startswith(b"DP1"):
        return _dpapi_crypt(blob[3:], protect=False)
    if blob.startswith(b"RAW"):
        return blob[3:]
    return blob   # formato desconocido: se intenta tal cual


def _clave_db_hex() -> str:
    """Llave de cifrado de la BD actual (hex). Se genera una sola vez y se
    cachea por ruta para respetar el monkeypatch de DB_PATH en tests."""
    if DB_PATH in _CLAVES_DB:
        return _CLAVES_DB[DB_PATH]
    kf = _ruta_llave()
    if os.path.exists(kf):
        raw = _desproteger_llave(open(kf, "rb").read())
    else:
        raw = os.urandom(32)
        open(kf, "wb").write(_proteger_llave(raw))
    _CLAVES_DB[DB_PATH] = raw.hex()
    return _CLAVES_DB[DB_PATH]


def _sql_lit(s) -> str:
    """Literal SQL seguro para valores que no admiten bind params (PRAGMAs)."""
    return "'" + str(s).replace("'", "''") + "'"


def _bd_esta_cifrada(path: str) -> bool:
    """Una BD en texto plano empieza con 'SQLite format 3'; una SQLCipher no."""
    try:
        with open(path, "rb") as f:
            return f.read(16) != b"SQLite format 3\x00"
    except OSError:
        return False


def archivo_cifrado(path: str) -> bool:
    return _bd_esta_cifrada(path)


def _conectar_archivo(path: str, password: str = None):
    """Conecta a un .db cualquiera aplicando la llave interna (o `password`
    si se indica) solo cuando el archivo está cifrado."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if CIFRADO_DB and _bd_esta_cifrada(path):
        if password:
            conn.execute(f"PRAGMA key = {_sql_lit(password)}")
        else:
            conn.execute(f"PRAGMA key = \"x'{_clave_db_hex()}'\"")
    return conn


def probar_clave_db(path: str, password: str = None) -> bool:
    """True si el archivo abre con la llave interna o el password dado."""
    try:
        conn = _conectar_archivo(path, password)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()
        return True
    except sqlite3.Error:
        return False


def _cifrar_archivo_db(path: str):
    """Convierte un archivo .db en texto plano a SQLCipher con la llave
    interna, verificando integridad antes de reemplazar el original."""
    key = _clave_db_hex()
    tmp = f"{path}.enc"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(path)          # sin key → abre el archivo en claro
    try:
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error:
            pass
        src.execute("PRAGMA journal_mode=DELETE").fetchone()
        src.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key}'\"", (tmp,))
        src.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        src.execute("DETACH DATABASE encrypted")
    finally:
        src.close()
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            os.remove(path + ext)
    chk = _conectar_archivo(tmp)
    ok = chk.execute("PRAGMA integrity_check").fetchone()
    chk.close()
    if not ok or ok[0] != "ok":
        os.remove(tmp)
        raise RuntimeError("Falló la verificación de la BD cifrada")
    os.replace(tmp, path)


def asegurar_cifrado() -> bool:
    """Migra la BD principal (y backups automáticos viejos) de texto plano
    a SQLCipher. Devuelve True si cifró la BD principal en esta llamada."""
    if not CIFRADO_DB or not os.path.exists(DB_PATH):
        return False
    migrada = False
    if not _bd_esta_cifrada(DB_PATH):
        _cifrar_archivo_db(DB_PATH)
        migrada = True
    if os.path.isdir(BACKUP_DIR):
        for f in os.listdir(BACKUP_DIR):
            p = os.path.join(BACKUP_DIR, f)
            if f.endswith(".db") and not _bd_esta_cifrada(p):
                try:
                    _cifrar_archivo_db(p)
                except Exception:
                    pass   # backup ilegible: se deja, no se pierde datos
    return migrada


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if CIFRADO_DB:
        conn.execute(f"PRAGMA key = \"x'{_clave_db_hex()}'\"")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ─── Sesión y autorización (Fase 3) ───────────────────────────────────────────
# La GUI establece la sesión tras el login. Las funciones de datos validan
# rol y scope de empresa AQUÍ — no dependen solo de ocultar botones.
_SESION = {"usuario_id": None, "rol": None, "empresa_id": None}


def establecer_sesion(user: dict):
    """Registra el usuario autenticado para el control de acceso."""
    _SESION.update(usuario_id=user.get("id"), rol=user.get("rol"),
                   empresa_id=user.get("empresa_id"))


def cerrar_sesion():
    _SESION.update(usuario_id=None, rol=None, empresa_id=None)


def sesion_actual():
    return dict(_SESION)


def _requerir_rol(*roles):
    if _SESION["rol"] not in roles:
        raise PermissionError(
            f"Esta acción requiere rol: {'/'.join(roles)}.")


def _verificar_scope_empresa(empresa_id):
    """El admin solo puede operar sobre su propia empresa; el superadmin
    sobre cualquiera. Anti-IDOR a nivel de datos."""
    _requerir_rol("admin", "superadmin")
    if _SESION["rol"] != "superadmin" and \
            int(empresa_id) != int(_SESION["empresa_id"] or -1):
        raise PermissionError(
            "No tienes permiso para operar sobre esta empresa.")


def _verificar_cuenta_propia(usuario_id):
    _requerir_rol("admin", "superadmin")
    if int(usuario_id) != int(_SESION["usuario_id"] or -1):
        raise PermissionError(
            "Solo puedes realizar esta acción sobre tu propia cuenta.")


def _empresa_de(conn, tabla, fila_id):
    """Resuelve el empresa_id de un registro para verificar scope cuando
    la función solo recibe el ID del registro."""
    row = conn.execute(f"SELECT empresa_id FROM {tabla} WHERE id=?",
                       (fila_id,)).fetchone()
    return row["empresa_id"] if row else None


def _verificar_scope_registro(conn, tabla, fila_id):
    """Scope para funciones que reciben solo el ID del registro.
    Si el registro no existe, se deja que la función lo maneje."""
    eid = _empresa_de(conn, tabla, fila_id)
    if eid is not None:
        _verificar_scope_empresa(eid)


def verificar_integridad():
    """Verifica la BD existente con PRAGMA integrity_check.

    Retorna (ok, detalle). Si no hay BD todavía, ok=True y detalle vacío."""
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return True, ""
    try:
        conn = _conectar_archivo(DB_PATH)
        res = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if res and res[0] == "ok":
            return True, ""
        return False, res[0] if res else "integrity_check sin resultado"
    except sqlite3.Error as e:
        if _bd_esta_cifrada(DB_PATH):
            return False, f"BD cifrada ilegible (¿falta {_ruta_llave()}?): {e}"
        return False, str(e)


def apartar_db_corrupta():
    """Renombra una BD corrupta para no perderla y arrancar con una limpia."""
    if not os.path.exists(DB_PATH):
        return None
    dest = f"{DB_PATH}-corrupta-{datetime.now():%Y%m%d_%H%M%S}"
    shutil.move(DB_PATH, dest)
    return dest


def backup_automatico(max_backups=MAX_AUTOBACKUPS):
    """Copia nomina.db a backups/ al arrancar, antes de migrar.
    Mantiene solo los últimos `max_backups`. Retorna la ruta o None."""
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return None
    dest = os.path.join(BACKUP_DIR, f"auto_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, dest)
    # Rotación: borrar los más viejos
    antiguos = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.startswith("auto_") and f.endswith(".db")),
        reverse=True)
    for viejo in antiguos[max_backups:]:
        try: os.remove(os.path.join(BACKUP_DIR, viejo))
        except OSError: pass
    return dest


def hash_password(password: str) -> str:
    """bcrypt con salt — resistente a fuerza bruta y rainbow tables."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _es_hash_legado(hashed: str) -> bool:
    """Detecta hashes SHA-256 sin salt del esquema anterior (64 hex)."""
    return bool(hashed) and len(hashed) == 64 and all(
        c in "0123456789abcdef" for c in hashed.lower())


def verify_password(password: str, hashed: str) -> bool:
    """Verifica contra bcrypt; acepta SHA-256 legado para migración."""
    if not hashed:
        return False
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(password.encode("utf-8"),
                                  hashed.encode("utf-8"))
        except ValueError:
            return False
    if _es_hash_legado(hashed):
        return hashlib.sha256(password.encode("utf-8")).hexdigest() == hashed
    return False


def init_db():
    """Inicializa la base de datos con todas las tablas necesarias."""
    asegurar_cifrado()
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS empresas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL,
            nit         TEXT NOT NULL UNIQUE,
            direccion   TEXT,
            telefono    TEXT,
            logo_path   TEXT,
            periodo_pago TEXT DEFAULT 'mensual' CHECK(periodo_pago IN ('quincenal','mensual')),
            correo      TEXT,
            correo_password TEXT,
            creado_en   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            nombre      TEXT NOT NULL,
            rol         TEXT NOT NULL CHECK(rol IN ('superadmin','admin')),
            empresa_id  INTEGER REFERENCES empresas(id) ON DELETE SET NULL,
            foto_path   TEXT,
            activo      INTEGER DEFAULT 1,
            intentos_fallidos     INTEGER DEFAULT 0,
            bloqueado_hasta       TEXT,
            debe_cambiar_password INTEGER DEFAULT 0,
            creado_en   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS empleados (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            nombre      TEXT NOT NULL,
            documento   TEXT NOT NULL,
            cargo       TEXT NOT NULL,
            salario     REAL NOT NULL,
            fecha_ingreso TEXT,
            tipo_cuenta TEXT,
            numero_cuenta TEXT,
            edad        INTEGER,
            correo      TEXT,
            tipo_vinculacion TEXT DEFAULT 'salario' CHECK(tipo_vinculacion IN ('salario','prestacion')),
            tarifa_diaria REAL,
            activo      INTEGER DEFAULT 1,
            creado_en   TEXT DEFAULT (datetime('now')),
            UNIQUE(empresa_id, documento)
        );

        CREATE TABLE IF NOT EXISTS bitacora (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id  INTEGER,
            usuario_id  INTEGER,
            usuario_nom TEXT,
            accion      TEXT NOT NULL,
            detalle     TEXT,
            fecha       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS nominas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            periodo         TEXT NOT NULL,
            quincena        INTEGER DEFAULT 0,
            dias_trabajados INTEGER NOT NULL DEFAULT 30,
            horas_extras    REAL NOT NULL DEFAULT 0,
            valor_hora_extra REAL NOT NULL DEFAULT 0,
            bonificaciones  REAL NOT NULL DEFAULT 0,
            deducciones     REAL NOT NULL DEFAULT 0,
            salud           REAL NOT NULL DEFAULT 0,
            pension         REAL NOT NULL DEFAULT 0,
            prestamo        REAL NOT NULL DEFAULT 0,
            auxilio_transporte REAL NOT NULL DEFAULT 0,
            salario_base    REAL NOT NULL,
            salario_bruto   REAL NOT NULL,
            salario_neto    REAL NOT NULL,
            observaciones   TEXT,
            colilla_path    TEXT,
            generado_en     TEXT DEFAULT (datetime('now')),
            UNIQUE(empresa_id, empleado_id, periodo, quincena)
        );

        CREATE TABLE IF NOT EXISTS prestamos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            monto       REAL NOT NULL,
            abonado     REAL NOT NULL DEFAULT 0,
            saldo       REAL NOT NULL,
            fecha       TEXT NOT NULL,
            estado      TEXT DEFAULT 'activo' CHECK(estado IN ('activo','pagado')),
            observaciones TEXT,
            creado_en   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS contratos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            descripcion     TEXT NOT NULL,
            valor_total     REAL NOT NULL,
            fecha_inicio    TEXT NOT NULL,
            fecha_fin       TEXT,
            deducciones     REAL NOT NULL DEFAULT 0,
            observaciones   TEXT,
            estado          TEXT DEFAULT 'activo' CHECK(estado IN ('activo','liquidado')),
            colilla_path    TEXT,
            nomina_id       INTEGER,
            creado_en       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS parametros_legales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            anio            INTEGER NOT NULL UNIQUE,
            smmlv           REAL NOT NULL,
            aux_transporte  REAL NOT NULL,
            uvt             REAL,
            pct_salud       REAL NOT NULL DEFAULT 4.0,
            pct_pension     REAL NOT NULL DEFAULT 4.0,
            pct_fsp         REAL NOT NULL DEFAULT 1.0,
            umbral_fsp_smmlv REAL NOT NULL DEFAULT 4.0,
            creado_en       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS novedades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            tipo            TEXT NOT NULL,
            fecha_inicio    TEXT NOT NULL,
            fecha_fin       TEXT,
            valor           REAL,
            observaciones   TEXT,
            estado          TEXT DEFAULT 'activa' CHECK(estado IN ('activa','cerrada')),
            creado_en       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS config_dian (
            empresa_id      INTEGER PRIMARY KEY REFERENCES empresas(id) ON DELETE CASCADE,
            prefijo         TEXT DEFAULT 'NE',
            consecutivo     INTEGER DEFAULT 0,
            ambiente        TEXT DEFAULT 'habilitacion'
                            CHECK(ambiente IN ('habilitacion','produccion')),
            software_id     TEXT,
            software_pin    TEXT,
            actualizado_en  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS documentos_dian (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            periodo         TEXT NOT NULL,
            tipo            TEXT NOT NULL DEFAULT 'nomina'
                            CHECK(tipo IN ('nomina','ajuste')),
            prefijo         TEXT,
            numero          TEXT,
            cune            TEXT,
            xml_path        TEXT,
            ambiente        TEXT,
            estado          TEXT DEFAULT 'generado'
                            CHECK(estado IN ('generado','transmitido','aceptado','rechazado')),
            fecha_envio     TEXT,
            fecha_respuesta TEXT,
            respuesta       TEXT,
            doc_origen_id   INTEGER REFERENCES documentos_dian(id),
            creado_en       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS meta (
            clave   TEXT PRIMARY KEY,
            valor   TEXT
        );
    """)

    # Crear superadmin por defecto si no existe
    c.execute("SELECT id FROM usuarios WHERE rol='superadmin' LIMIT 1")
    if not c.fetchone():
        c.execute(
            """INSERT INTO usuarios (username, password, nombre, rol,
               debe_cambiar_password) VALUES (?,?,?,?,1)""",
            ("superadmin", hash_password(PASSWORD_FABRICA), "Super Administrador", "superadmin")
        )

    c.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES ('schema_version', ?)",
              (str(SCHEMA_VERSION),))
    c.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES ('app_version', ?)",
              (APP_VERSION,))
    c.execute("PRAGMA journal_mode=WAL").fetchone()

    conn.commit()
    conn.close()


def migrar_base_datos():
    """Agrega nuevas columnas a tablas existentes."""
    conn = get_connection()
    c = conn.cursor()
    
    # Agregar columna logo_path a empresas si no existe
    try:
        c.execute("ALTER TABLE empresas ADD COLUMN logo_path TEXT")
    except sqlite3.OperationalError:
        pass  # La columna ya existe
    
    # Agregar columna foto_path a usuarios si no existe
    try:
        c.execute("ALTER TABLE usuarios ADD COLUMN foto_path TEXT")
    except sqlite3.OperationalError:
        pass  # La columna ya existe
    
    # Agregar nuevas columnas a empleados si no existen
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN tipo_cuenta TEXT")
    except sqlite3.OperationalError:
        pass
    
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN numero_cuenta TEXT")
    except sqlite3.OperationalError:
        pass
    
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN edad INTEGER")
    except sqlite3.OperationalError:
        pass
    
    # Agregar columna auxilio_transporte a nominas si no existe
    try:
        c.execute("ALTER TABLE nominas ADD COLUMN auxilio_transporte REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    
    # Agregar columna periodo_pago a empresas si no existe
    try:
        c.execute("ALTER TABLE empresas ADD COLUMN periodo_pago TEXT DEFAULT 'mensual'")
    except sqlite3.OperationalError:
        pass
    
    # Agregar columna quincena a nominas si no existe
    try:
        c.execute("ALTER TABLE nominas ADD COLUMN quincena INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    
    # Agregar columna prestamo a nominas si no existe
    try:
        c.execute("ALTER TABLE nominas ADD COLUMN prestamo REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    
    # Agregar columna empresa_id a bitacora si no existe
    try:
        c.execute("ALTER TABLE bitacora ADD COLUMN empresa_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # Seguridad de sesión: lockout y cambio de contraseña obligatorio
    for ddl in (
        "ALTER TABLE usuarios ADD COLUMN intentos_fallidos INTEGER DEFAULT 0",
        "ALTER TABLE usuarios ADD COLUMN bloqueado_hasta TEXT",
        "ALTER TABLE usuarios ADD COLUMN debe_cambiar_password INTEGER DEFAULT 0",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Marcar superadmin que aún use la contraseña de fábrica (hash SHA-256 legado)
    sha_fabrica = hashlib.sha256(PASSWORD_FABRICA.encode("utf-8")).hexdigest()
    c.execute("""UPDATE usuarios SET debe_cambiar_password=1
                 WHERE rol='superadmin' AND password=?""", (sha_fabrica,))

    # Migrar prestamos: si la tabla tiene el esquema antiguo (columna cuota), reconstruir
    try:
        columns = [row[1] for row in c.execute("PRAGMA table_info(prestamos)").fetchall()]

        if 'cuota' in columns:
            # Esquema antiguo detectado - reconstruir tabla
            c.execute("DROP TABLE IF EXISTS prestamos_new")
            c.execute("""
                CREATE TABLE prestamos_new (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa_id  INTEGER NOT NULL,
                    empleado_id INTEGER NOT NULL,
                    monto       REAL NOT NULL,
                    abonado     REAL NOT NULL DEFAULT 0,
                    saldo       REAL NOT NULL,
                    fecha       TEXT NOT NULL,
                    estado      TEXT DEFAULT 'activo' CHECK(estado IN ('activo','pagado')),
                    observaciones TEXT,
                    creado_en   TEXT DEFAULT (datetime('now'))
                )
            """)

            # Migrar datos existentes
            c.execute("""
                INSERT INTO prestamos_new (id, empresa_id, empleado_id, monto, abonado, saldo, fecha, estado, observaciones, creado_en)
                SELECT id, empresa_id, empleado_id, monto,
                       COALESCE(cuotas_pagadas * cuota, 0) as abonado,
                       COALESCE(monto - (cuotas_pagadas * cuota), monto) as saldo,
                       fecha, estado, observaciones, creado_en
                FROM prestamos
            """)

            c.execute("DROP TABLE prestamos")
            c.execute("ALTER TABLE prestamos_new RENAME TO prestamos")
        elif 'abonado' not in columns:
            # Tabla existe pero sin columnas nuevas
            try:
                c.execute("ALTER TABLE prestamos ADD COLUMN abonado REAL NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE prestamos ADD COLUMN saldo REAL")
            except sqlite3.OperationalError:
                pass
            # Calcular saldo para registros existentes
            try:
                c.execute("UPDATE prestamos SET saldo = monto - abonado WHERE saldo IS NULL")
            except sqlite3.OperationalError:
                pass
    except Exception:
        pass

    # Agregar correo y correo_password a empresas si no existen
    try:
        c.execute("ALTER TABLE empresas ADD COLUMN correo TEXT")
    except sqlite3.OperationalError:
        pass

    # Nominación exacta: FSP e IBC (auxilio_transporte ya existe desde antes)
    for ddl in (
        "ALTER TABLE nominas ADD COLUMN fsp REAL NOT NULL DEFAULT 0",
        "ALTER TABLE nominas ADD COLUMN ibc REAL",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Sembrar parámetros legales por año (valores oficiales Colombia)
    c.execute("""
        INSERT OR IGNORE INTO parametros_legales
            (anio, smmlv, aux_transporte, uvt, pct_salud, pct_pension, pct_fsp, umbral_fsp_smmlv)
        VALUES
            (2025, 1423500, 200000, 49799, 4.0, 4.0, 1.0, 4.0),
            (2026, 1750905, 249095, 52374, 4.0, 4.0, 1.0, 4.0)
    """)

    # Fase 5 — campos DIAN en empresas y empleados
    for ddl in (
        "ALTER TABLE empresas ADD COLUMN departamento TEXT",
        "ALTER TABLE empresas ADD COLUMN municipio TEXT",
        "ALTER TABLE empresas ADD COLUMN pais TEXT DEFAULT 'CO'",
        "ALTER TABLE empleados ADD COLUMN primer_nombre TEXT",
        "ALTER TABLE empleados ADD COLUMN otros_nombres TEXT",
        "ALTER TABLE empleados ADD COLUMN primer_apellido TEXT",
        "ALTER TABLE empleados ADD COLUMN segundo_apellido TEXT",
        "ALTER TABLE empleados ADD COLUMN tipo_documento TEXT DEFAULT '13'",
        "ALTER TABLE empleados ADD COLUMN tipo_trabajador TEXT DEFAULT '01'",
        "ALTER TABLE empleados ADD COLUMN subtipo_trabajador TEXT DEFAULT '00'",
        "ALTER TABLE empleados ADD COLUMN tipo_contrato TEXT DEFAULT '1'",
        "ALTER TABLE empleados ADD COLUMN municipio_laboral TEXT",
        "ALTER TABLE empleados ADD COLUMN alto_riesgo_pension INTEGER DEFAULT 0",
        "ALTER TABLE empleados ADD COLUMN eps TEXT",
        "ALTER TABLE empleados ADD COLUMN arl TEXT",
        "ALTER TABLE empleados ADD COLUMN fondo_pension TEXT",
        "ALTER TABLE empleados ADD COLUMN fondo_cesantias TEXT",
        "ALTER TABLE empleados ADD COLUMN banco TEXT",
        "ALTER TABLE empleados ADD COLUMN fecha_retiro TEXT",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Repartir nombres existentes en campos DIAN (heurística simple:
    # primer token → primer_nombre, último → primer_apellido, resto → otros_nombres)
    rows = c.execute(
        "SELECT id, nombre FROM empleados WHERE primer_nombre IS NULL").fetchall()
    for rid, nombre in rows:
        toks = [t for t in (nombre or "").strip().split() if t]
        pn = toks[0] if toks else ""
        pa = toks[-1] if len(toks) > 1 else ""
        on = " ".join(toks[1:-1]) if len(toks) > 2 else ""
        c.execute(
            "UPDATE empleados SET primer_nombre=?, otros_nombres=?, primer_apellido=? WHERE id=?",
            (pn, on or None, pa, rid))

    # Un solo documento de nómina por empleado y período (las notas de
    # ajuste sí pueden ser varias por documento)
    try:
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_dian_nomina
            ON documentos_dian(empresa_id, empleado_id, periodo)
            WHERE tipo='nomina'
        """)
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE empresas ADD COLUMN correo_password TEXT")
    except sqlite3.OperationalError:
        pass

    # Agregar columna colilla_path a nominas si no existe
    try:
        c.execute("ALTER TABLE nominas ADD COLUMN colilla_path TEXT")
    except sqlite3.OperationalError:
        pass

    # Crear tabla contratos si no existe (nueva funcionalidad)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS contratos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id      INTEGER NOT NULL,
            empleado_id     INTEGER NOT NULL,
            descripcion     TEXT NOT NULL,
            valor_total     REAL NOT NULL,
            fecha_inicio    TEXT NOT NULL,
            fecha_fin       TEXT,
            deducciones     REAL NOT NULL DEFAULT 0,
            observaciones   TEXT,
            estado          TEXT DEFAULT 'activo',
            colilla_path    TEXT,
            nomina_id       INTEGER,
            creado_en       TEXT DEFAULT (datetime('now'))
        )""")
    except sqlite3.OperationalError:
        pass

    # Agregar correo a empleados si no existe
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN correo TEXT")
    except sqlite3.OperationalError:
        pass

    # Agregar tipo_vinculacion, tarifa_diaria y valor_contrato a empleados si no existen
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN tipo_vinculacion TEXT DEFAULT 'salario'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE empleados ADD COLUMN tarifa_diaria REAL")
    except sqlite3.OperationalError:
        pass
    try:
        # valor_contrato era el nombre antiguo — se mantiene para compatibilidad
        c.execute("ALTER TABLE empleados ADD COLUMN valor_contrato REAL")
    except sqlite3.OperationalError:
        pass
    # Copiar valor_contrato → tarifa_diaria donde no se haya hecho aún
    c.execute("""UPDATE empleados SET tarifa_diaria=valor_contrato
                 WHERE tarifa_diaria IS NULL AND valor_contrato IS NOT NULL""")
    # Renombrar tipo 'contrato' → 'prestacion' (datos de versión anterior)
    c.execute("UPDATE empleados SET tipo_vinculacion='prestacion' WHERE tipo_vinculacion='contrato'")
    # Asegurar que NULL quede como 'salario'
    c.execute("UPDATE empleados SET tipo_vinculacion='salario' WHERE tipo_vinculacion IS NULL OR tipo_vinculacion=''")
    conn.commit()

    # Agregar restricción UNIQUE a nominas (empresa_id, empleado_id, periodo, quincena)
    # SQLite no permite ALTER TABLE para agregar constraints, pero el INSERT en guardar_nomina
    # ya valida duplicados por código — la restricción está en la tabla nueva.

    conn.commit()
    conn.close()


# ─── EMPRESAS ────────────────────────────────────────────────────────────────

def crear_empresa(nombre, nit, direccion, telefono, logo_path=None, periodo_pago='mensual',
                  correo=None, correo_password=None, departamento=None, municipio=None):
    _requerir_rol("superadmin")
    if correo_password:
        correo_password = cifrar_secreto(correo_password)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO empresas (nombre, nit, direccion, telefono, logo_path,
               periodo_pago, correo, correo_password, departamento, municipio)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (nombre, nit, direccion, telefono, logo_path, periodo_pago, correo,
             correo_password, departamento, municipio)
        )
        conn.commit()
        return True, "Empresa creada exitosamente."
    except sqlite3.IntegrityError:
        return False, "Ya existe una empresa con ese NIT."
    finally:
        conn.close()


def listar_empresas():
    _requerir_rol("superadmin")
    conn = get_connection()
    rows = conn.execute("SELECT * FROM empresas ORDER BY nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_empresa(empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute("SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def actualizar_empresa(empresa_id, nombre, nit, direccion, telefono, logo_path=None,
                       periodo_pago='mensual', correo=None, correo_password=None,
                       departamento=None, municipio=None):
    _requerir_rol("superadmin")
    if correo_password:
        correo_password = cifrar_secreto(correo_password)
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE empresas SET nombre=?, nit=?, direccion=?, telefono=?, logo_path=?,
               periodo_pago=?, correo=?, correo_password=?, departamento=?, municipio=?
               WHERE id=?""",
            (nombre, nit, direccion, telefono, logo_path, periodo_pago,
             correo, correo_password, departamento, municipio, empresa_id)
        )
        conn.commit()
        return True, "Empresa actualizada."
    except sqlite3.IntegrityError:
        return False, "Ya existe una empresa con ese NIT."
    finally:
        conn.close()


def eliminar_empresa(empresa_id):
    _requerir_rol("superadmin")
    conn = get_connection()
    conn.execute("DELETE FROM empresas WHERE id=?", (empresa_id,))
    conn.commit()
    conn.close()
    return True, "Empresa eliminada."


# ─── USUARIOS ────────────────────────────────────────────────────────────────

def crear_admin(username, password, nombre, empresa_id, foto_path=None):
    _requerir_rol("superadmin")
    ok_p, msg_p = politica_password_ok(password)
    if not ok_p:
        return False, msg_p
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO usuarios (username, password, nombre, rol, empresa_id, foto_path) VALUES (?,?,?,?,?,?)",
            (username, hash_password(password), nombre, "admin", empresa_id, foto_path)
        )
        conn.commit()
        return True, "Administrador creado exitosamente."
    except sqlite3.IntegrityError:
        return False, "El nombre de usuario ya existe."
    finally:
        conn.close()


def listar_admins():
    _requerir_rol("superadmin")
    conn = get_connection()
    rows = conn.execute("""
        SELECT u.*, e.nombre as empresa_nombre
        FROM usuarios u
        LEFT JOIN empresas e ON u.empresa_id = e.id
        WHERE u.rol = 'admin'
        ORDER BY u.nombre
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def eliminar_usuario(usuario_id):
    _requerir_rol("superadmin")
    conn = get_connection()
    conn.execute("DELETE FROM usuarios WHERE id=? AND rol='admin'", (usuario_id,))
    conn.commit()
    conn.close()
    return True, "Administrador eliminado."


def cambiar_password(usuario_id, nueva_password):
    """Cambio por un tercero (superadmin resetea admin): obliga a que el
    usuario la cambie en su próximo login."""
    _requerir_rol("superadmin")
    ok_p, msg_p = politica_password_ok(nueva_password)
    if not ok_p:
        return False, msg_p
    conn = get_connection()
    conn.execute("""UPDATE usuarios SET password=?, debe_cambiar_password=1,
                    intentos_fallidos=0, bloqueado_hasta=NULL WHERE id=?""",
                 (hash_password(nueva_password), usuario_id))
    conn.commit()
    conn.close()
    return True, "Contraseña actualizada correctamente."


def cambiar_password_propio(usuario_id, password_actual, nueva_password):
    _verificar_cuenta_propia(usuario_id)
    ok_p, msg_p = politica_password_ok(nueva_password)
    if not ok_p:
        return False, msg_p
    conn = get_connection()
    row = conn.execute("SELECT password FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
    conn.close()
    if not row or not verify_password(password_actual, row["password"]):
        return False, "La contraseña actual es incorrecta."
    conn = get_connection()
    conn.execute("""UPDATE usuarios SET password=?, debe_cambiar_password=0,
                    intentos_fallidos=0, bloqueado_hasta=NULL WHERE id=?""",
                 (hash_password(nueva_password), usuario_id))
    conn.commit()
    conn.close()
    return True, "Contraseña actualizada correctamente."


def verificar_password_usuario(usuario_id, password) -> bool:
    """Re-autenticación puntual (operaciones destructivas). No afecta
    contadores de bloqueo ni hace rehash."""
    _verificar_cuenta_propia(usuario_id)
    conn = get_connection()
    row = conn.execute("SELECT password FROM usuarios WHERE id=?",
                       (usuario_id,)).fetchone()
    conn.close()
    return bool(row) and verify_password(password, row["password"])


def toggle_activo(usuario_id):
    _requerir_rol("superadmin")
    conn = get_connection()
    row = conn.execute("SELECT activo FROM usuarios WHERE id=? AND rol='admin'", (usuario_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Usuario no encontrado."
    nuevo = 0 if row["activo"] else 1
    conn.execute("UPDATE usuarios SET activo=? WHERE id=?", (nuevo, usuario_id))
    conn.commit()
    conn.close()
    estado = "activado" if nuevo else "desactivado"
    return True, f"Administrador {estado} correctamente."


def obtener_usuario(usuario_id):
    _requerir_rol("admin", "superadmin")
    if _SESION["rol"] != "superadmin":
        _verificar_cuenta_propia(usuario_id)
    conn = get_connection()
    row = conn.execute("""
        SELECT u.*, e.nombre as empresa_nombre
        FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id
        WHERE u.id=?
    """, (usuario_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _col(row, nombre, default=None):
    """Acceso seguro a columnas de sqlite3.Row (BD aún no migrada)."""
    try:
        v = row[nombre]
    except (IndexError, KeyError):
        return default
    return default if v is None else v


def usuario_bloqueado(username) -> int:
    """Minutos restantes de bloqueo de la cuenta (0 si no está bloqueada
    o el usuario no existe)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT bloqueado_hasta FROM usuarios WHERE username=?",
            (username,)).fetchone()
    except sqlite3.OperationalError:
        row = None  # columna aún no migrada
    conn.close()
    if not row or not row["bloqueado_hasta"]:
        return 0
    try:
        hasta = datetime.strptime(row["bloqueado_hasta"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0
    resta = (hasta - datetime.utcnow()).total_seconds()
    return int(resta // 60) + 1 if resta > 0 else 0


def autenticar(username, password):
    """Login con lockout progresivo, migración SHA-256→bcrypt y bitácora."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE username=? AND activo=1", (username,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    ahora = datetime.utcnow()

    # Cuenta bloqueada temporalmente
    hasta = _col(row, "bloqueado_hasta")
    if hasta:
        try:
            bloqueado = datetime.strptime(hasta, "%Y-%m-%d %H:%M:%S") > ahora
        except ValueError:
            bloqueado = False
        if bloqueado:
            conn.close()
            registrar_bitacora(row["id"], row["nombre"],
                "Intento de login en cuenta bloqueada", "",
                empresa_id=row["empresa_id"])
            return None

    # ── Credenciales correctas ──
    if verify_password(password, row["password"]):
        # Rehash transparente si venía en SHA-256 legado
        nuevo_hash = (hash_password(password)
                      if _es_hash_legado(row["password"]) else row["password"])
        debe_cambiar = (1 if password == PASSWORD_FABRICA
                        else (_col(row, "debe_cambiar_password", 0) or 0))
        conn.execute("""UPDATE usuarios SET password=?, intentos_fallidos=0,
                        bloqueado_hasta=NULL, debe_cambiar_password=? WHERE id=?""",
                     (nuevo_hash, debe_cambiar, row["id"]))
        conn.commit()
        conn.close()
        registrar_bitacora(row["id"], row["nombre"], "Inició sesión", "",
                           empresa_id=row["empresa_id"])
        user = dict(row)
        user["debe_cambiar_password"] = debe_cambiar
        return user

    # ── Credenciales incorrectas ──
    intentos = (_col(row, "intentos_fallidos", 0) or 0) + 1
    if intentos >= MAX_INTENTOS_LOGIN:
        bloqueo = (ahora + timedelta(minutes=MINUTOS_BLOQUEO)).strftime(
            "%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE usuarios SET intentos_fallidos=0, bloqueado_hasta=? WHERE id=?",
            (bloqueo, row["id"]))
        detalle = f"{MINUTOS_BLOQUEO} min por {MAX_INTENTOS_LOGIN} intentos fallidos"
        conn.commit(); conn.close()
        registrar_bitacora(row["id"], row["nombre"], "Cuenta bloqueada",
                           detalle, empresa_id=row["empresa_id"])
    else:
        conn.execute("UPDATE usuarios SET intentos_fallidos=? WHERE id=?",
                     (intentos, row["id"]))
        conn.commit(); conn.close()
        registrar_bitacora(row["id"], row["nombre"],
            f"Intento de login fallido ({intentos}/{MAX_INTENTOS_LOGIN})",
            "", empresa_id=row["empresa_id"])
    return None


# ─── EMPLEADOS ───────────────────────────────────────────────────────────────

def _split_nombre(nombre):
    """Reparte un nombre completo en (primer_nombre, otros_nombres,
    primer_apellido) — heurística usada para campos DIAN."""
    toks = [t for t in (nombre or "").split() if t]
    return (toks[0] if toks else None,
            " ".join(toks[1:-1]) if len(toks) > 2 else None,
            toks[-1] if len(toks) > 1 else None)


def crear_empleado(empresa_id, nombre, documento, cargo, salario, fecha_ingreso,
                   tipo_cuenta=None, numero_cuenta=None, edad=None,
                   tipo_vinculacion='salario', tarifa_diaria=None, correo=None,
                   dian=None):
    _verificar_scope_empresa(empresa_id)
    dian = {k: v for k, v in (dian or {}).items() if k in _EMP_DIAN_COLS}
    pn, on, pa = _split_nombre(nombre)
    if not dian.get("primer_nombre"):   dian["primer_nombre"] = pn
    if not dian.get("otros_nombres") and on:  dian["otros_nombres"] = on
    if not dian.get("primer_apellido"): dian["primer_apellido"] = pa
    cols = list(dian.keys()); vals = list(dian.values())
    extra_cols = (", " + ", ".join(cols)) if cols else ""
    extra_marks = ("," + ",".join("?" * len(cols))) if cols else ""
    conn = get_connection()
    try:
        conn.execute(
            f"""INSERT INTO empleados (empresa_id, nombre, documento, cargo, salario,
               fecha_ingreso, tipo_cuenta, numero_cuenta, edad,
               tipo_vinculacion, tarifa_diaria, correo{extra_cols})
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?{extra_marks})""",
            (empresa_id, nombre, documento, cargo, pesos(salario), fecha_ingreso,
             tipo_cuenta, numero_cuenta, edad, tipo_vinculacion,
             pesos(tarifa_diaria) if tarifa_diaria else tarifa_diaria, correo,
             *vals)
        )
        conn.commit()
        return True, "Empleado registrado exitosamente."
    except sqlite3.IntegrityError:
        return False, "Ya existe un empleado con ese documento en esta empresa."
    finally:
        conn.close()


def obtener_empleado(empleado_id, empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM empleados WHERE id=? AND empresa_id=?", (empleado_id, empresa_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def actualizar_empleado(emp_id, empresa_id, nombre, documento, cargo, salario, fecha_ingreso,
                        tipo_cuenta=None, numero_cuenta=None, edad=None,
                        tipo_vinculacion='salario', tarifa_diaria=None, correo=None,
                        dian=None):
    _verificar_scope_empresa(empresa_id)
    dian = {k: v for k, v in (dian or {}).items() if k in _EMP_DIAN_COLS}
    if dian:
        pn, on, pa = _split_nombre(nombre)
        if not dian.get("primer_nombre"):   dian["primer_nombre"] = pn
        if not dian.get("otros_nombres") and on:  dian["otros_nombres"] = on
        if not dian.get("primer_apellido"): dian["primer_apellido"] = pa
    extra_sets = (", " + ", ".join(f"{k}=?" for k in dian)) if dian else ""
    conn = get_connection()
    try:
        conn.execute(
            f"""UPDATE empleados SET nombre=?, documento=?, cargo=?, salario=?,
               fecha_ingreso=?, tipo_cuenta=?, numero_cuenta=?, edad=?,
               tipo_vinculacion=?, tarifa_diaria=?, correo=?{extra_sets}
               WHERE id=? AND empresa_id=?""",
            (nombre, documento, cargo, pesos(salario), fecha_ingreso,
             tipo_cuenta, numero_cuenta, edad, tipo_vinculacion,
             pesos(tarifa_diaria) if tarifa_diaria else tarifa_diaria, correo,
             *dian.values(), emp_id, empresa_id)
        )
        conn.commit()
        return True, "Empleado actualizado."
    except sqlite3.IntegrityError:
        return False, "Ya existe un empleado con ese documento."
    finally:
        conn.close()


def eliminar_empleado(emp_id, empresa_id):
    """Inactiva un empleado (cambia activo=0)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    conn.execute("UPDATE empleados SET activo=0 WHERE id=? AND empresa_id=?", (emp_id, empresa_id))
    conn.commit()
    conn.close()
    return True, "Empleado desactivado."


def eliminar_empleado_definitivo(emp_id, empresa_id, eliminar_nominas=False):
    """Elimina completamente un empleado y opcionalmente sus registros de nómina."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        # Verificar si existen nóminas asociadas
        nominas_count = conn.execute(
            "SELECT COUNT(*) FROM nominas WHERE empleado_id=? AND empresa_id=?",
            (emp_id, empresa_id)
        ).fetchone()[0]
        docs_count = conn.execute(
            "SELECT COUNT(*) FROM documentos_dian WHERE empleado_id=? AND empresa_id=?",
            (emp_id, empresa_id)
        ).fetchone()[0]

        if (nominas_count > 0 or docs_count > 0) and not eliminar_nominas:
            detalle = f"{nominas_count} nómina(s)"
            if docs_count:
                detalle += f" y {docs_count} documento(s) DIAN"
            return False, (f"El empleado tiene {detalle}. Debe decidir qué hacer "
                           "con estos registros.")

        # Eliminar registros de nómina (y documentos DIAN, que referencian al
        # empleado por FK) si se solicita — los XML quedan en disco
        if eliminar_nominas:
            conn.execute("DELETE FROM nominas WHERE empleado_id=? AND empresa_id=?",
                         (emp_id, empresa_id))
            if docs_count:
                conn.execute(
                    "DELETE FROM documentos_dian WHERE empleado_id=? AND empresa_id=?",
                    (emp_id, empresa_id))
        
        # Eliminar al empleado
        cur = conn.execute("DELETE FROM empleados WHERE id=? AND empresa_id=?", (emp_id, empresa_id))
        if cur.rowcount == 0:
            conn.rollback()
            return False, "Empleado no encontrado."
        conn.commit()
        
        msg = "Empleado eliminado definitivamente."
        if eliminar_nominas:
            msg += f" Se eliminaron {nominas_count} registros de nómina asociados."
        else:
            msg += " No se encontraron registros de nómina asociados."
            
        return True, msg
    except Exception as e:
        return False, f"Error al eliminar empleado: {str(e)}"
    finally:
        conn.close()


def toggle_activo_empleado(emp_id, empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute("SELECT activo FROM empleados WHERE id=? AND empresa_id=?", (emp_id, empresa_id)).fetchone()
    if not row:
        conn.close()
        return False, "Empleado no encontrado."
    nuevo = 0 if row["activo"] else 1
    conn.execute("UPDATE empleados SET activo=? WHERE id=? AND empresa_id=?", (nuevo, emp_id, empresa_id))
    conn.commit()
    conn.close()
    estado = "activado" if nuevo else "desactivado"
    return True, f"Empleado {estado} correctamente."


def cambiar_estado_empleado(emp_id, empresa_id, activo):
    """Establece el estado activo/inactivo de un empleado."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    cur = conn.execute(
        "UPDATE empleados SET activo=? WHERE id=? AND empresa_id=?",
        (1 if activo else 0, emp_id, empresa_id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return False, "Empleado no encontrado."
    return True, f"Empleado {'activado' if activo else 'desactivado'} correctamente."


# ─── NÓMINA ──────────────────────────────────────────────────────────────────

def guardar_nomina(empresa_id, empleado_id, periodo, dias_trabajados, horas_extras,
                   valor_hora_extra, bonificaciones, deducciones, observaciones, salario_base,
                   quincena=0, prestamo=0, periodo_pago='mensual'):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        # Verificar si ya existe nómina para este empleado en este período/quincena
        existente = conn.execute(
            "SELECT id FROM nominas WHERE empresa_id=? AND empleado_id=? AND periodo=? AND quincena=?",
            (empresa_id, empleado_id, periodo, quincena)
        ).fetchone()
        if existente:
            return False, f"Ya existe una nómina para este empleado en el período {periodo}.", existente["id"]

        calc = calcular_nomina(salario_base, dias_trabajados, horas_extras,
                               valor_hora_extra, bonificaciones, deducciones,
                               periodo_pago=periodo_pago, quincena=quincena,
                               prestamo=prestamo, periodo=periodo)
        conn.execute("""
            INSERT INTO nominas (empresa_id, empleado_id, periodo, quincena, dias_trabajados,
                horas_extras, valor_hora_extra, bonificaciones, deducciones,
                salud, pension, fsp, ibc, auxilio_transporte, prestamo,
                salario_base, salario_bruto, salario_neto, observaciones)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            empresa_id, empleado_id, periodo, quincena, dias_trabajados,
            horas_extras, pesos(valor_hora_extra), pesos(bonificaciones), pesos(deducciones),
            calc["salud"], calc["pension"], calc["fsp"], calc["ibc"],
            calc["aux_transporte"], calc["prestamo"], pesos(salario_base),
            calc["salario_bruto"], calc["salario_neto"], observaciones
        ))
        conn.commit()
        nomina_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        if prestamo > 0:
            # Verificar que aún hay saldo pendiente antes de descontar
            saldo_real = conn.execute(
                "SELECT COALESCE(SUM(saldo),0) FROM prestamos WHERE empresa_id=? AND empleado_id=? AND estado='activo'",
                (empresa_id, empleado_id)).fetchone()[0]
            if saldo_real > 0:
                _actualizar_cuota_prestamo(conn, empresa_id, empleado_id,
                                           min(D(prestamo), D(saldo_real)))
            conn.commit()

        return True, "Nómina generada exitosamente.", nomina_id
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def obtener_nomina_existente(empresa_id, empleado_id, periodo, quincena=0):
    """Retorna la nómina existente de un empleado en un período, o None."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute("""
        SELECT n.*, e.nombre as empleado_nombre, e.cargo, e.documento, e.fecha_ingreso,
               e.tipo_cuenta, e.numero_cuenta, e.edad,
               em.nombre as empresa_nombre, em.nit, em.direccion, em.telefono, em.logo_path,
               em.periodo_pago
        FROM nominas n
        JOIN empleados e ON n.empleado_id = e.id
        JOIN empresas em ON n.empresa_id = em.id
        WHERE n.empresa_id=? AND n.empleado_id=? AND n.periodo=? AND n.quincena=?
    """, (empresa_id, empleado_id, periodo, quincena)).fetchone()
    conn.close()
    return dict(row) if row else None


def obtener_nomina(nomina_id, empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute("""
        SELECT n.*, e.nombre as empleado_nombre, e.cargo, e.documento, e.fecha_ingreso,
               e.tipo_cuenta, e.numero_cuenta, e.edad,
               em.nombre as empresa_nombre, em.nit, em.direccion, em.telefono, em.logo_path,
               em.periodo_pago
        FROM nominas n
        JOIN empleados e ON n.empleado_id = e.id
        JOIN empresas em ON n.empresa_id = em.id
        WHERE n.id=? AND n.empresa_id=?
    """, (nomina_id, empresa_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def listar_periodos(empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT periodo FROM nominas WHERE empresa_id=? ORDER BY periodo DESC",
        (empresa_id,)
    ).fetchall()
    conn.close()
    return [r["periodo"] for r in rows]


def estadisticas_empresa(empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    stats = {}
    stats["total_empleados"] = conn.execute(
        "SELECT COUNT(*) FROM empleados WHERE empresa_id=? AND activo=1", (empresa_id,)
    ).fetchone()[0]
    stats["total_nominas"] = conn.execute(
        "SELECT COUNT(*) FROM nominas WHERE empresa_id=?", (empresa_id,)
    ).fetchone()[0]
    row = conn.execute(
        "SELECT SUM(salario_neto), MAX(periodo) FROM nominas WHERE empresa_id=?", (empresa_id,)
    ).fetchone()
    stats["total_pagado"] = pesos(row[0] or 0)
    stats["ultimo_periodo"] = row[1] or "—"
    conn.close()
    return stats


# ─── FUNCIONES ADICIONALES ───────────────────────────────────────────────────

def actualizar_admin(usuario_id, username, nombre, empresa_id, foto_path=None):
    """Edita datos de un administrador (sin tocar contraseña)."""
    _requerir_rol("superadmin")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE usuarios SET username=?, nombre=?, empresa_id=?, foto_path=? WHERE id=? AND rol='admin'",
            (username, nombre, empresa_id, foto_path, usuario_id)
        )
        conn.commit()
        return True, "Administrador actualizado correctamente."
    except sqlite3.IntegrityError:
        return False, "El nombre de usuario ya está en uso por otro administrador."
    finally:
        conn.close()


def listar_empleados(empresa_id, incluir_inactivos=False):
    """Lista empleados activos o todos (activos + inactivos)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtro = "" if incluir_inactivos else "AND activo=1"
    rows = conn.execute(
        f"SELECT * FROM empleados WHERE empresa_id=? {filtro} ORDER BY nombre",
        (empresa_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_empleados_por_tipo(empresa_id, tipo_vinculacion=None, incluir_inactivos=False):
    """Lista empleados filtrando por tipo de vinculación: 'salario', 'contrato' o None (todos)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtros = ["empresa_id=?"]
    params = [empresa_id]
    if not incluir_inactivos:
        filtros.append("activo=1")
    if tipo_vinculacion:
        filtros.append("tipo_vinculacion=?")
        params.append(tipo_vinculacion)
    where = " AND ".join(filtros)
    rows = conn.execute(
        f"SELECT * FROM empleados WHERE {where} ORDER BY nombre", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def calcular_pago_prestacion(tarifa_diaria, dias_trabajados, bonificaciones=0, deducciones=0, prestamo=0):
    """Calcula pago para empleado de prestación de servicios (cobra por día).
    No aplica deducciones de salud ni pensión — solo préstamos activos."""
    bruto = D(tarifa_diaria) * D(dias_trabajados) + D(bonificaciones)
    neto  = bruto - D(deducciones) - D(prestamo)
    return {
        "tarifa_diaria":   pesos(tarifa_diaria),
        "dias_trabajados": dias_trabajados,
        "salario_bruto":   pesos(bruto),
        "salud":           0.0,
        "pension":         0.0,
        "prestamo":        pesos(prestamo),
        "salario_neto":    pesos(neto),
    }


def guardar_nomina_prestacion(empresa_id, empleado_id, periodo, tarifa_diaria,
                               dias_trabajados, bonificaciones=0, deducciones=0,
                               prestamo=0, observaciones=None):
    """Guarda el pago de un empleado de prestación de servicios (quincena=98).
    No aplica deducciones de ley — solo descuenta préstamos activos si hay."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        existente = conn.execute(
            "SELECT id FROM nominas WHERE empresa_id=? AND empleado_id=? AND periodo=? AND quincena=98",
            (empresa_id, empleado_id, periodo)
        ).fetchone()
        if existente:
            return False, f"Ya existe un pago de prestación para este empleado en {periodo}.", existente["id"]

        calc = calcular_pago_prestacion(tarifa_diaria, dias_trabajados, bonificaciones, deducciones, prestamo)
        obs  = observaciones or f"Prestación de servicios — {dias_trabajados} día(s) × $ {float(tarifa_diaria):,.0f}/día"

        conn.execute("""
            INSERT INTO nominas (empresa_id, empleado_id, periodo, quincena,
                dias_trabajados, horas_extras, valor_hora_extra, bonificaciones,
                deducciones, salud, pension, prestamo, salario_base,
                salario_bruto, salario_neto, observaciones)
            VALUES (?,?,?,98, ?,0,0, ?,?, 0,0,?, ?,?,?,?)
        """, (empresa_id, empleado_id, periodo, int(dias_trabajados),
              pesos(bonificaciones), pesos(deducciones), pesos(prestamo),
              pesos(tarifa_diaria), calc["salario_bruto"], calc["salario_neto"], obs))
        conn.commit()
        nomina_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        if prestamo > 0:
            _actualizar_cuota_prestamo(conn, empresa_id, empleado_id, float(prestamo))
            conn.commit()

        return True, "Pago de prestación registrado exitosamente.", nomina_id
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def guardar_nomina_contrato(empresa_id, empleado_id, periodo, valor_total,
                             deducciones, observaciones):
    """Guarda la liquidación final de un contrato como nómina especial (quincena=99)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        existente = conn.execute(
            "SELECT id FROM nominas WHERE empresa_id=? AND empleado_id=? AND periodo=? AND quincena=99",
            (empresa_id, empleado_id, periodo)
        ).fetchone()
        if existente:
            return False, "Ya existe una liquidación de contrato para este empleado en este período.", existente["id"]

        salario_neto = pesos(D(valor_total) - D(deducciones))
        conn.execute("""
            INSERT INTO nominas (empresa_id, empleado_id, periodo, quincena, dias_trabajados,
                horas_extras, valor_hora_extra, bonificaciones, deducciones,
                salud, pension, prestamo, salario_base, salario_bruto, salario_neto, observaciones)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            empresa_id, empleado_id, periodo, 99, 0,
            0, 0, 0, pesos(deducciones),
            0, 0, 0, pesos(valor_total), pesos(valor_total), salario_neto,
            observaciones or "Liquidación de contrato"
        ))
        conn.commit()
        nomina_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return True, "Liquidación de contrato generada exitosamente.", nomina_id
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def obtener_reporte_empleados(empresa_id, incluir_inactivos=False):
    """Obtiene información completa de empleados para reporte."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtro = "" if incluir_inactivos else "AND e.activo=1"
    rows = conn.execute(
        f"""
        SELECT e.*, emp.nombre as empresa_nombre, emp.nit as empresa_nit
        FROM empleados e
        JOIN empresas emp ON e.empresa_id = emp.id
        WHERE e.empresa_id=? {filtro}
        ORDER BY e.nombre
        """,
        (empresa_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_nominas(empresa_id, periodo=None, empleado_id=None):
    """Lista nóminas con filtros opcionales de período y empleado.
    Ordena por generado_en DESC para mostrar los más recientes primero."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtros = ["n.empresa_id=?"]
    params  = [empresa_id]
    if periodo:
        filtros.append("n.periodo=?")
        params.append(periodo)
    if empleado_id:
        filtros.append("n.empleado_id=?")
        params.append(empleado_id)
    where = " AND ".join(filtros)
    rows = conn.execute(f"""
        SELECT n.*, e.nombre as empleado_nombre, e.cargo, e.documento
        FROM nominas n JOIN empleados e ON n.empleado_id = e.id
        WHERE {where}
        ORDER BY n.generado_en DESC, e.nombre
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def limpiar_nominas_duplicadas(empresa_id):
    """Elimina nóminas duplicadas dejando solo la más reciente por empleado+período+quincena.
    Retorna cuántos duplicados eliminó."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        # Encontrar grupos duplicados: mismo empleado, período y quincena
        duplicados = conn.execute("""
            SELECT empleado_id, periodo, quincena, COUNT(*) as cnt, MAX(id) as keep_id
            FROM nominas
            WHERE empresa_id=?
            GROUP BY empleado_id, periodo, quincena
            HAVING COUNT(*) > 1
        """, (empresa_id,)).fetchall()

        total_eliminados = 0
        for dup in duplicados:
            # Eliminar todos excepto el más reciente (MAX id)
            result = conn.execute("""
                DELETE FROM nominas
                WHERE empresa_id=? AND empleado_id=? AND periodo=? AND quincena=? AND id != ?
            """, (empresa_id, dup["empleado_id"], dup["periodo"], dup["quincena"], dup["keep_id"]))
            total_eliminados += result.rowcount

        conn.commit()
        return total_eliminados
    except Exception:
        return 0
    finally:
        conn.close()


def eliminar_nomina(nomina_id, empresa_id):
    """Elimina un registro de nómina definitivamente."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    conn.execute("DELETE FROM nominas WHERE id=? AND empresa_id=?", (nomina_id, empresa_id))
    conn.commit()
    conn.close()
    return True, "Registro de nómina eliminado."


# ─── FUNCIONES DE PERÍODO AUTOMÁTICO ─────────────────────────────────────────────

def obtener_periodo_actual():
    """Obtiene el período actual en formato YYYY-MM."""
    return datetime.now().strftime("%Y-%m")


def obtener_periodos_disponibles(empresa_id):
    """Obtiene todos los períodos disponibles para una empresa, ordenados cronológicamente."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT periodo FROM nominas WHERE empresa_id=? ORDER BY periodo DESC",
        (empresa_id,)
    ).fetchall()
    conn.close()
    return [r["periodo"] for r in rows]


def verificar_nuevo_periodo(empresa_id):
    """Verifica si el período actual tiene nóminas registradas."""
    _verificar_scope_empresa(empresa_id)
    periodo_actual = obtener_periodo_actual()
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM nominas WHERE empresa_id=? AND periodo=?",
        (empresa_id, periodo_actual)
    ).fetchone()[0]
    conn.close()
    return count == 0, periodo_actual


def obtener_ultimo_periodo_registrado(empresa_id):
    """Obtiene el último período registrado en la base de datos."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(periodo) as ultimo FROM nominas WHERE empresa_id=?",
        (empresa_id,)
    ).fetchone()
    conn.close()
    return row["ultimo"] if row["ultimo"] else None


def generar_lista_periodos_con_actual(empresa_id):
    """Genera lista de períodos incluyendo el actual con indicadores."""
    periodo_actual = obtener_periodo_actual()
    periodos_existentes = obtener_periodos_disponibles(empresa_id)
    
    # Asegurar que el período actual esté en la lista
    if periodo_actual not in periodos_existentes:
        periodos_existentes.append(periodo_actual)
    
    # Ordenar y añadir indicadores
    periodos_ordenados = sorted(periodos_existentes, reverse=True)
    periodos_con_info = []
    
    for periodo in periodos_ordenados:
        if periodo == periodo_actual:
            periodos_con_info.append(f"{periodo} 📅 (Actual)")
        else:
            periodos_con_info.append(periodo)
    
    return periodos_ordenados, periodos_con_info


# ─── FUNCIONES PARA MANEJO DE IMÁGENES ────────────────────────────────────────

def crear_carpetas_imagenes():
    """Crea las carpetas necesarias para almacenar imágenes."""
    import os
    from paths import APP_DIR
    
    logos_dir = os.path.join(APP_DIR, "logos")
    fotos_dir = os.path.join(APP_DIR, "fotos")
    
    os.makedirs(logos_dir, exist_ok=True)
    os.makedirs(fotos_dir, exist_ok=True)
    
    return logos_dir, fotos_dir


def guardar_imagen(file_path, carpeta_tipo, empresa_id=None, usuario_id=None):
    """Guarda una imagen en la carpeta correspondiente y retorna la ruta."""
    import os
    import shutil
    from datetime import datetime
    from paths import APP_DIR

    if empresa_id is not None:
        _verificar_scope_empresa(empresa_id)
    else:
        _requerir_rol("admin", "superadmin")
    if not file_path or not os.path.exists(file_path):
        return None
    
    # Determinar carpeta de destino
    if carpeta_tipo == "logo":
        destino_dir = os.path.join(APP_DIR, "logos")
        if empresa_id:
            nombre_archivo = f"logo_empresa_{empresa_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        else:
            nombre_archivo = f"logo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    elif carpeta_tipo == "foto":
        destino_dir = os.path.join(APP_DIR, "fotos")
        if usuario_id:
            nombre_archivo = f"foto_usuario_{usuario_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        else:
            nombre_archivo = f"foto_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    else:
        return None
    
    # Crear carpeta si no existe
    os.makedirs(destino_dir, exist_ok=True)
    
    # Copiar archivo
    destino_path = os.path.join(destino_dir, nombre_archivo)
    try:
        shutil.copy2(file_path, destino_path)
        return destino_path
    except Exception as e:
        print(f"Error al guardar imagen: {e}")
        return None


def eliminar_imagen(imagen_path):
    """Elimina una imagen del sistema de archivos."""
    import os
    
    if imagen_path and os.path.exists(imagen_path):
        try:
            os.remove(imagen_path)
            return True
        except Exception as e:
            print(f"Error al eliminar imagen: {e}")
            return False
    return True


# ─── BITÁCORA DE AUDITORÍA ───────────────────────────────────────────────────

def registrar_bitacora(usuario_id, usuario_nom, accion, detalle="", empresa_id=None):
    """Registra una acción en la bitácora de auditoría."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bitacora (empresa_id, usuario_id, usuario_nom, accion, detalle) VALUES (?,?,?,?,?)",
            (empresa_id, usuario_id, usuario_nom, accion, detalle)
        )
        conn.commit()
    except Exception:
        pass  # La bitácora no debe interrumpir operaciones
    finally:
        conn.close()


def listar_bitacora(limite=200, empresa_id=None):
    """Lista los últimos registros de la bitácora. Si empresa_id es None, trae todos (superadmin)."""
    if empresa_id is None:
        _requerir_rol("superadmin")
    else:
        _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    if empresa_id is not None:
        rows = conn.execute(
            "SELECT * FROM bitacora WHERE empresa_id=? ORDER BY fecha DESC LIMIT ?", (empresa_id, limite)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bitacora ORDER BY fecha DESC LIMIT ?", (limite,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── BACKUP / RESTAURACIÓN ───────────────────────────────────────────────────

def exportar_backup(ruta_destino, password=None):
    """Copia la base de datos completa a la ruta indicada (superadmin).
    El archivo queda cifrado; con `password` se re-cifra con esa contraseña
    para poder restaurarlo en otro equipo."""
    _requerir_rol("superadmin")
    try:
        shutil.copy2(DB_PATH, ruta_destino)
        if CIFRADO_DB and password:
            conn = _conectar_archivo(ruta_destino)
            conn.execute(f"PRAGMA rekey = {_sql_lit(password)}")
            conn.close()
        return True, f"Backup completo exportado a:\n{ruta_destino}"
    except Exception as e:
        return False, f"Error al exportar backup: {e}"


def exportar_backup_empresa(empresa_id, ruta_destino, password=None):
    """Exporta solo los datos de una empresa a un archivo .db separado.
    El archivo queda cifrado (llave interna o `password` si se indica)."""
    _verificar_scope_empresa(empresa_id)
    try:
        conn_src = get_connection()
        import sqlite3 as _sql
        conn_dst = _sql.connect(ruta_destino)
        conn_dst.row_factory = _sql.Row
        if CIFRADO_DB:
            if password:
                conn_dst.execute(f"PRAGMA key = {_sql_lit(password)}")
            else:
                conn_dst.execute(
                    f"PRAGMA key = \"x'{_clave_db_hex()}'\"")

        conn_dst.executescript("""
            CREATE TABLE IF NOT EXISTS empresas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL, nit TEXT, direccion TEXT, telefono TEXT,
                logo_path TEXT, periodo_pago TEXT DEFAULT 'mensual',
                correo TEXT, correo_password TEXT,
                departamento TEXT, municipio TEXT, pais TEXT DEFAULT 'CO',
                creado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS empleados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, nombre TEXT NOT NULL, documento TEXT NOT NULL,
                cargo TEXT NOT NULL, salario REAL NOT NULL, fecha_ingreso TEXT,
                tipo_cuenta TEXT, numero_cuenta TEXT, edad INTEGER, correo TEXT,
                tipo_vinculacion TEXT DEFAULT 'salario', tarifa_diaria REAL, valor_contrato REAL,
                primer_nombre TEXT, otros_nombres TEXT, primer_apellido TEXT,
                segundo_apellido TEXT, tipo_documento TEXT DEFAULT '13',
                tipo_trabajador TEXT DEFAULT '01', subtipo_trabajador TEXT DEFAULT '00',
                tipo_contrato TEXT DEFAULT '1', municipio_laboral TEXT,
                alto_riesgo_pension INTEGER DEFAULT 0, eps TEXT, arl TEXT,
                fondo_pension TEXT, fondo_cesantias TEXT, banco TEXT, fecha_retiro TEXT,
                activo INTEGER DEFAULT 1, creado_en TEXT DEFAULT (datetime('now')),
                UNIQUE(empresa_id, documento)
            );
            CREATE TABLE IF NOT EXISTS nominas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, empleado_id INTEGER NOT NULL,
                periodo TEXT NOT NULL, quincena INTEGER DEFAULT 0,
                dias_trabajados INTEGER, horas_extras REAL, valor_hora_extra REAL,
                bonificaciones REAL, deducciones REAL, salud REAL, pension REAL,
                fsp REAL DEFAULT 0, ibc REAL, auxilio_transporte REAL DEFAULT 0,
                prestamo REAL DEFAULT 0, salario_base REAL, salario_bruto REAL,
                salario_neto REAL, observaciones TEXT, colilla_path TEXT,
                generado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS novedades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, empleado_id INTEGER NOT NULL,
                tipo TEXT NOT NULL, fecha_inicio TEXT NOT NULL, fecha_fin TEXT,
                valor REAL, observaciones TEXT, estado TEXT DEFAULT 'activa',
                creado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS config_dian (
                empresa_id INTEGER PRIMARY KEY, prefijo TEXT DEFAULT 'NE',
                consecutivo INTEGER DEFAULT 0, ambiente TEXT DEFAULT 'habilitacion',
                software_id TEXT, software_pin TEXT,
                actualizado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS documentos_dian (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, empleado_id INTEGER NOT NULL,
                periodo TEXT NOT NULL, tipo TEXT DEFAULT 'nomina', prefijo TEXT,
                numero TEXT, cune TEXT, xml_path TEXT, ambiente TEXT,
                estado TEXT DEFAULT 'generado', fecha_envio TEXT,
                fecha_respuesta TEXT, respuesta TEXT, doc_origen_id INTEGER,
                creado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS prestamos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, empleado_id INTEGER NOT NULL,
                monto REAL NOT NULL, abonado REAL NOT NULL DEFAULT 0,
                saldo REAL NOT NULL, fecha TEXT NOT NULL,
                estado TEXT DEFAULT 'activo', observaciones TEXT,
                creado_en TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bitacora (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER, usuario_id INTEGER, usuario_nom TEXT,
                accion TEXT NOT NULL, detalle TEXT,
                fecha TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contratos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL, empleado_id INTEGER NOT NULL,
                descripcion TEXT NOT NULL, valor_total REAL NOT NULL,
                fecha_inicio TEXT NOT NULL, fecha_fin TEXT,
                deducciones REAL NOT NULL DEFAULT 0, observaciones TEXT,
                estado TEXT DEFAULT 'activo', colilla_path TEXT, nomina_id INTEGER,
                creado_en TEXT DEFAULT (datetime('now'))
            );
        """)

        def _copy_table(table, where_col=None, where_val=None):
            if where_col:
                rows = conn_src.execute(
                    f"SELECT * FROM {table} WHERE {where_col}=?", (where_val,)
                ).fetchall()
            else:
                rows = conn_src.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                return
            # Get column names from source
            cols = [d[0] for d in conn_src.execute(
                f"SELECT * FROM {table} LIMIT 0").description]
            # Get destination column names
            dst_cols = [d[0] for d in conn_dst.execute(
                f"SELECT * FROM {table} LIMIT 0").description]
            for row in rows:
                row_dict = dict(zip(cols, tuple(row)))
                # Only insert columns that exist in destination
                common = [c for c in dst_cols if c in row_dict]
                vals = [row_dict[c] for c in common]
                ph = ",".join(["?"]*len(common))
                col_str = ",".join(common)
                conn_dst.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({ph})", vals)

        # Empresa
        empresa_row = conn_src.execute(
            "SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
        if empresa_row:
            emp_dict = dict(zip(
                [d[0] for d in conn_src.execute("SELECT * FROM empresas LIMIT 0").description],
                tuple(empresa_row)))
            dst_cols = [d[0] for d in conn_dst.execute(
                "SELECT * FROM empresas LIMIT 0").description]
            common = [c for c in dst_cols if c in emp_dict]
            vals = [emp_dict[c] for c in common]
            ph = ",".join(["?"]*len(common))
            col_str = ",".join(common)
            conn_dst.execute(f"INSERT OR IGNORE INTO empresas ({col_str}) VALUES ({ph})", vals)

        _copy_table("empleados", "empresa_id", empresa_id)
        _copy_table("nominas",   "empresa_id", empresa_id)
        _copy_table("prestamos", "empresa_id", empresa_id)
        _copy_table("bitacora",  "empresa_id", empresa_id)
        _copy_table("contratos", "empresa_id", empresa_id)
        _copy_table("novedades", "empresa_id", empresa_id)
        _copy_table("config_dian", "empresa_id", empresa_id)
        _copy_table("documentos_dian", "empresa_id", empresa_id)

        conn_dst.commit()
        conn_dst.close()
        conn_src.close()
        return True, f"Backup de empresa exportado a:\n{ruta_destino}"
    except Exception as e:
        return False, f"Error al exportar backup de empresa: {e}"


def importar_backup(ruta_origen, password=None):
    """Restaura la base de datos completa desde un backup (superadmin).
    Acepta backups en texto plano, cifrados con la llave interna o con una
    contraseña de exportación (`password`); el resultado siempre queda con
    la llave interna de esta instalación."""
    _requerir_rol("superadmin")
    try:
        tmp = DB_PATH + ".import"
        shutil.copy2(ruta_origen, tmp)
        if CIFRADO_DB:
            if _bd_esta_cifrada(tmp):
                if not probar_clave_db(tmp):
                    if password and probar_clave_db(tmp, password):
                        conn = _conectar_archivo(tmp, password)
                        conn.execute(
                            f"PRAGMA rekey = \"x'{_clave_db_hex()}'\"")
                        conn.close()
                    else:
                        os.remove(tmp)
                        return False, ("El backup está cifrado y no se pudo "
                                       "descifrar. Indica la contraseña de "
                                       "exportación si la tiene.")
            else:
                _cifrar_archivo_db(tmp)   # backup antiguo en claro
        os.replace(tmp, DB_PATH)
        return True, "Base de datos restaurada correctamente.\nReinicie la aplicación para ver los cambios."
    except Exception as e:
        return False, f"Error al importar backup: {e}"


def importar_backup_empresa(empresa_id, ruta_origen, password=None):
    """Restaura datos de empresa desde backup. Usa dict() para evitar sqlite3.Row.get() errors."""
    _verificar_scope_empresa(empresa_id)
    try:
        try:
            conn_src = _conectar_archivo(ruta_origen, password)
            conn_src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error:
            return False, ("No se pudo abrir el archivo de backup: está "
                           "cifrado con otra contraseña o está dañado.")
        conn_dst = get_connection()

        def _get_rows(conn, table, where_col=None, where_val=None):
            """Retorna lista de dicts — nunca sqlite3.Row — evita el bug .get()"""
            if where_col:
                rows = conn.execute(f"SELECT * FROM {table} WHERE {where_col}=?", (where_val,)).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                return []
            cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
            return [dict(zip(cols, tuple(r))) for r in rows]

        # Verificar empresa en backup
        emp_rows = _get_rows(conn_src, "empresas")
        emp_dict = next((r for r in emp_rows if r.get("id") == empresa_id), None)
        if not emp_dict:
            conn_src.close(); conn_dst.close()
            return False, "El archivo de backup no contiene datos de esta empresa."

        # Borrar datos actuales de la empresa (documentos/novedades antes que
        # empleados por las FK)
        for tbl in ("bitacora","documentos_dian","novedades","config_dian",
                    "prestamos","contratos","nominas","empleados"):
            try:
                conn_dst.execute(f"DELETE FROM {tbl} WHERE empresa_id=?", (empresa_id,))
            except Exception:
                pass  # tabla puede no existir en BD destino vieja

        # Actualizar empresa
        dst_emp_cols = [d[0] for d in conn_dst.execute("SELECT * FROM empresas LIMIT 0").description]
        common = [c for c in dst_emp_cols if c in emp_dict and c != "id"]
        vals   = [emp_dict[c] for c in common]
        sets   = ", ".join(f"{c}=?" for c in common)
        conn_dst.execute(f"UPDATE empresas SET {sets} WHERE id=?", vals + [empresa_id])

        # Copiar tablas remapeando los ids autoincrementales.
        # Así un backup hecho en otra instalación no colisiona con los ids
        # de otras empresas ni queda con referencias rotas (empleado_id, nomina_id).
        def _dst_cols(table):
            try:
                return set(d[0] for d in conn_dst.execute(f"SELECT * FROM {table} LIMIT 0").description)
            except Exception:
                return None  # tabla no existe en destino

        def _insert(table, row):
            """Inserta row sin su id. Retorna el nuevo id o None si falla."""
            dst_cols = _dst_cols(table)
            if dst_cols is None:
                return None
            cols_ok = [c for c in row.keys() if c in dst_cols and c != "id"]
            v = [row[c] for c in cols_ok]
            try:
                cur = conn_dst.execute(
                    f"INSERT INTO {table} ({','.join(cols_ok)}) "
                    f"VALUES ({','.join(['?']*len(cols_ok))})", v)
                return cur.lastrowid
            except Exception:
                return None

        emp_map = {}  # empleados: id_viejo -> id_nuevo
        nom_map = {}  # nominas:   id_viejo -> id_nuevo

        for row in _get_rows(conn_src, "empleados", "empresa_id", empresa_id):
            old_id = row.pop("id", None)
            row["empresa_id"] = empresa_id
            new_id = _insert("empleados", row)
            if old_id is not None and new_id is not None:
                emp_map[old_id] = new_id

        for row in _get_rows(conn_src, "nominas", "empresa_id", empresa_id):
            old_id = row.pop("id", None)
            row["empresa_id"] = empresa_id
            row["empleado_id"] = emp_map.get(row.get("empleado_id"))
            if row["empleado_id"] is None:
                continue  # nómina huérfana: el empleado no está en el backup
            new_id = _insert("nominas", row)
            if old_id is not None and new_id is not None:
                nom_map[old_id] = new_id

        for row in _get_rows(conn_src, "prestamos", "empresa_id", empresa_id):
            row.pop("id", None)
            row["empresa_id"] = empresa_id
            row["empleado_id"] = emp_map.get(row.get("empleado_id"))
            if row["empleado_id"] is None:
                continue
            _insert("prestamos", row)

        for row in _get_rows(conn_src, "contratos", "empresa_id", empresa_id):
            row.pop("id", None)
            row["empresa_id"] = empresa_id
            row["empleado_id"] = emp_map.get(row.get("empleado_id"))
            if row["empleado_id"] is None:
                continue
            row["nomina_id"] = nom_map.get(row.get("nomina_id"))
            _insert("contratos", row)

        for row in _get_rows(conn_src, "bitacora", "empresa_id", empresa_id):
            row.pop("id", None)
            row["empresa_id"] = empresa_id
            _insert("bitacora", row)

        for row in _get_rows(conn_src, "novedades", "empresa_id", empresa_id):
            row.pop("id", None)
            row["empresa_id"] = empresa_id
            row["empleado_id"] = emp_map.get(row.get("empleado_id"))
            if row["empleado_id"] is None:
                continue
            _insert("novedades", row)

        for row in _get_rows(conn_src, "config_dian", "empresa_id", empresa_id):
            row["empresa_id"] = empresa_id
            _insert("config_dian", row)

        doc_map = {}  # documentos_dian: id_viejo -> id_nuevo (para doc_origen_id)
        docs_src = sorted(
            _get_rows(conn_src, "documentos_dian", "empresa_id", empresa_id),
            key=lambda r: r.get("id") or 0)
        for row in docs_src:
            old_id = row.pop("id", None)
            row["empresa_id"] = empresa_id
            row["empleado_id"] = emp_map.get(row.get("empleado_id"))
            row["doc_origen_id"] = doc_map.get(row.get("doc_origen_id"))
            if row["empleado_id"] is None:
                continue
            new_id = _insert("documentos_dian", row)
            if old_id is not None and new_id is not None:
                doc_map[old_id] = new_id

        conn_dst.commit()
        conn_src.close(); conn_dst.close()
        return True, "Datos restaurados correctamente.\nReinicia la aplicación para ver los cambios."
    except Exception as e:
        return False, f"Error al restaurar backup de empresa: {e}"


# ─── DEDUCCIONES DE LEY ──────────────────────────────────────────────────────

# Valores oficiales Colombia 2026 (fallback si la tabla parametros_legales
# está vacía — la fuente de verdad es la tabla, editable por el superadmin)
SALARIO_MINIMO_2026 = 1_750_905        # Decreto 1469 de 2025
AUXILIO_TRANSPORTE_2026 = 249_095      # Decreto 1470 de 2025

# Legado: ya no se usan en el cálculo (salud/pensión son % del IBC desde Fase 4)
DEDUCCION_SALUD_QUINCENA = 70_036
DEDUCCION_PENSION_QUINCENA = 70_036

# Tipos de novedad admitidos (alineados a conceptos DIAN del anexo técnico)
TIPOS_NOVEDAD = {
    "ingreso":                  "Ingreso del trabajador",
    "retiro":                   "Retiro del trabajador",
    "vacaciones":               "Vacaciones",
    "incapacidad":              "Incapacidad",
    "licencia_maternidad":      "Licencia de maternidad",
    "licencia_paternidad":      "Licencia de paternidad",
    "licencia_no_remunerada":   "Licencia no remunerada",
    "suspension":               "Suspensión del contrato",
    "variacion_salarial":       "Variación salarial",
    "otra":                     "Otra novedad",
}


# ─── PARÁMETROS LEGALES (Fase 4) ─────────────────────────────────────────────

def _parametros_default():
    return {"anio": datetime.now().year, "smmlv": SALARIO_MINIMO_2026,
            "aux_transporte": AUXILIO_TRANSPORTE_2026, "uvt": 52374,
            "pct_salud": 4.0, "pct_pension": 4.0, "pct_fsp": 1.0,
            "umbral_fsp_smmlv": 4.0}


def parametros_vigentes(periodo=None):
    """Parámetros legales del año de `periodo` ('YYYY-MM' o 'YYYY-MM-DD');
    sin periodo, del año actual. Si el año no está sembrado, usa el más
    reciente anterior a ese año (o el primero disponible); como último
    recurso, los valores por defecto del código."""
    anio = None
    if periodo:
        try:
            anio = int(str(periodo)[:4])
        except (ValueError, IndexError):
            anio = None
    if anio is None:
        anio = datetime.now().year
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM parametros_legales WHERE anio<=? ORDER BY anio DESC LIMIT 1",
            (anio,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM parametros_legales ORDER BY anio ASC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        row = None   # tabla aún no existe (migración en curso)
    finally:
        conn.close()
    return dict(row) if row else _parametros_default()


def listar_parametros_legales():
    _requerir_rol("superadmin")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM parametros_legales ORDER BY anio DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guardar_parametros_legales(anio, smmlv, aux_transporte, uvt=None,
                               pct_salud=4.0, pct_pension=4.0,
                               pct_fsp=1.0, umbral_fsp_smmlv=4.0):
    """Crea o actualiza los parámetros legales de un año (superadmin)."""
    _requerir_rol("superadmin")
    if not anio or int(anio) < 2000:
        return False, "Año inválido."
    if D(smmlv) <= 0 or D(aux_transporte) < 0:
        return False, "SMMLV y auxilio de transporte deben ser positivos."
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO parametros_legales
                (anio, smmlv, aux_transporte, uvt, pct_salud, pct_pension,
                 pct_fsp, umbral_fsp_smmlv)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(anio) DO UPDATE SET
                smmlv=excluded.smmlv, aux_transporte=excluded.aux_transporte,
                uvt=excluded.uvt, pct_salud=excluded.pct_salud,
                pct_pension=excluded.pct_pension, pct_fsp=excluded.pct_fsp,
                umbral_fsp_smmlv=excluded.umbral_fsp_smmlv
        """, (int(anio), pesos(smmlv), pesos(aux_transporte),
              pesos(uvt) if uvt else None,
              float(pct_salud), float(pct_pension), float(pct_fsp),
              float(umbral_fsp_smmlv)))
        conn.commit()
        return True, f"Parámetros del año {anio} guardados."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def eliminar_parametros_legales(param_id):
    _requerir_rol("superadmin")
    conn = get_connection()
    conn.execute("DELETE FROM parametros_legales WHERE id=?", (param_id,))
    conn.commit()
    conn.close()
    return True, "Parámetros eliminados."


# ─── NOVEDADES (Fase 4) ──────────────────────────────────────────────────────

def crear_novedad(empresa_id, empleado_id, tipo, fecha_inicio,
                  fecha_fin=None, valor=None, observaciones=None):
    """Registra una novedad de nómina (incapacidad, licencia, vacaciones…)."""
    _verificar_scope_empresa(empresa_id)
    if tipo not in TIPOS_NOVEDAD:
        return False, f"Tipo de novedad inválido. Válidos: {', '.join(TIPOS_NOVEDAD)}."
    if not fecha_inicio:
        return False, "La fecha de inicio es obligatoria."
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO novedades (empresa_id, empleado_id, tipo, fecha_inicio,
                fecha_fin, valor, observaciones)
            VALUES (?,?,?,?,?,?,?)
        """, (empresa_id, empleado_id, tipo, fecha_inicio, fecha_fin,
              pesos(valor) if valor is not None else None, observaciones))
        conn.commit()
        return True, "Novedad registrada."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def listar_novedades(empresa_id, empleado_id=None, estado=None):
    _verificar_scope_empresa(empresa_id)
    filtros = ["n.empresa_id=?"]; params = [empresa_id]
    if empleado_id:
        filtros.append("n.empleado_id=?"); params.append(empleado_id)
    if estado:
        filtros.append("n.estado=?");   params.append(estado)
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT n.*, e.nombre as empleado_nombre, e.documento
        FROM novedades n JOIN empleados e ON n.empleado_id = e.id
        WHERE {' AND '.join(filtros)}
        ORDER BY n.fecha_inicio DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_novedad(novedad_id):
    conn = get_connection()
    _verificar_scope_registro(conn, "novedades", novedad_id)
    row = conn.execute(
        "SELECT * FROM novedades WHERE id=?", (novedad_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def actualizar_novedad(novedad_id, tipo, fecha_inicio, fecha_fin=None,
                       valor=None, observaciones=None, estado=None):
    conn = get_connection()
    _verificar_scope_registro(conn, "novedades", novedad_id)
    if tipo not in TIPOS_NOVEDAD:
        return False, "Tipo de novedad inválido."
    try:
        if estado is not None:
            conn.execute(
                "UPDATE novedades SET estado=? WHERE id=?", (estado, novedad_id))
        conn.execute("""
            UPDATE novedades SET tipo=?, fecha_inicio=?, fecha_fin=?,
                valor=?, observaciones=? WHERE id=?
        """, (tipo, fecha_inicio, fecha_fin,
              pesos(valor) if valor is not None else None,
              observaciones, novedad_id))
        conn.commit()
        return True, "Novedad actualizada."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def eliminar_novedad(novedad_id):
    conn = get_connection()
    _verificar_scope_registro(conn, "novedades", novedad_id)
    conn.execute("DELETE FROM novedades WHERE id=?", (novedad_id,))
    conn.commit()
    conn.close()
    return True, "Novedad eliminada."


# ─── DIAN — NÓMINA ELECTRÓNICA (Fase 5) ──────────────────────────────────────
# Base de datos y generación del Documento Soporte de Pago de Nómina
# Electrónica (Resolución 000013 de 2021 + Anexo Técnico). La estructura XML
# generada es la del anexo v1.0 y DEBE validarse contra el set de pruebas
# del ambiente de habilitación antes de producción.

TIPOS_DOCUMENTO_DIAN = {
    "13": "CC — Cédula de ciudadanía",
    "22": "CE — Cédula de extranjería",
    "41": "Pasaporte",
    "42": "Doc. identificación extranjero",
    "47": "PEP — Permiso especial",
}
TIPOS_TRABAJADOR_DIAN = {
    "01": "Dependiente",
    "02": "Servicio doméstico",
    "04": "Madre comunitaria",
    "12": "Aprendiz SENA — etapa lectiva",
    "19": "Aprendiz SENA — etapa productiva",
    "22": "Docente oficial",
    "23": "Residente / postgrado en salud",
    "57": "Independiente",
}
SUBTIPOS_TRABAJADOR_DIAN = {
    "00": "No aplica",
    "01": "Dependiente pensionado por vejez activo",
    "02": "Independiente pensionado por vejez activo",
    "03": "No obligado a pensiones por discapacidad",
    "04": "Pensionado fuerzas militares",
    "05": "Pensionado policía nacional",
}
TIPOS_CONTRATO_DIAN = {
    "1": "Término fijo",
    "2": "Término indefinido",
    "3": "Obra o labor contratada",
    "4": "Aprendizaje",
    "5": "Prácticas laborales",
}
METODOS_PAGO_DIAN = {
    "10": "Débito bancario",
    "42": "Consignación bancaria",
    "20": "Tarjeta débito",
    "1":  "Efectivo",
}

# Campos DIAN editables del maestro de empleados
_EMP_DIAN_COLS = (
    "primer_nombre", "otros_nombres", "primer_apellido", "segundo_apellido",
    "tipo_documento", "tipo_trabajador", "subtipo_trabajador", "tipo_contrato",
    "municipio_laboral", "alto_riesgo_pension", "eps", "arl", "fondo_pension",
    "fondo_cesantias", "banco", "fecha_retiro",
)


def actualizar_empleado_dian(emp_id, empresa_id, **campos):
    """Actualiza solo los campos DIAN de un empleado."""
    _verificar_scope_empresa(empresa_id)
    cols = {k: v for k, v in campos.items() if k in _EMP_DIAN_COLS}
    if not cols:
        return False, "Sin campos DIAN que actualizar."
    sets = ", ".join(f"{k}=?" for k in cols)
    conn = get_connection()
    conn.execute(f"UPDATE empleados SET {sets} WHERE id=? AND empresa_id=?",
                 (*cols.values(), emp_id, empresa_id))
    conn.commit()
    conn.close()
    return True, "Datos DIAN actualizados."


def validar_empleado_dian(emp):
    """Devuelve lista de campos DIAN faltantes en el dict del empleado."""
    faltantes = []
    requeridos = [
        ("primer_nombre",    "Primer nombre"),
        ("primer_apellido",  "Primer apellido"),
        ("tipo_documento",   "Tipo de documento"),
        ("documento",        "Número de documento"),
        ("tipo_trabajador",  "Tipo de trabajador"),
        ("tipo_contrato",    "Tipo de contrato"),
        ("municipio_laboral","Municipio de trabajo (Divipola)"),
        ("eps",              "EPS"),
        ("arl",              "ARL"),
        ("fondo_pension",    "Fondo de pensiones"),
        ("fondo_cesantias",  "Fondo de cesantías"),
        ("banco",            "Banco"),
        ("numero_cuenta",    "Número de cuenta"),
        ("tipo_cuenta",      "Tipo de cuenta"),
        ("fecha_ingreso",    "Fecha de ingreso"),
    ]
    for clave, etiqueta in requeridos:
        if not emp.get(clave):
            faltantes.append(etiqueta)
    return faltantes


# ─── Configuración DIAN por empresa ──────────────────────────────────────────

def obtener_config_dian(empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM config_dian WHERE empresa_id=?", (empresa_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"empresa_id": empresa_id, "prefijo": "NE", "consecutivo": 0,
            "ambiente": "habilitacion", "software_id": None, "software_pin": None}


def guardar_config_dian(empresa_id, prefijo="NE", consecutivo=0,
                        ambiente="habilitacion", software_id=None, software_pin=None):
    _verificar_scope_empresa(empresa_id)
    if ambiente not in ("habilitacion", "produccion"):
        return False, "Ambiente inválido (habilitacion|produccion)."
    conn = get_connection()
    conn.execute("""
        INSERT INTO config_dian (empresa_id, prefijo, consecutivo, ambiente,
            software_id, software_pin)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(empresa_id) DO UPDATE SET
            prefijo=excluded.prefijo, consecutivo=excluded.consecutivo,
            ambiente=excluded.ambiente, software_id=excluded.software_id,
            software_pin=excluded.software_pin
    """, (empresa_id, prefijo or "NE", int(consecutivo or 0),
          ambiente, software_id, software_pin))
    conn.commit()
    conn.close()
    return True, "Configuración DIAN guardada."


# ─── CUNE y generación del documento mensual ─────────────────────────────────

def _fmt2(x):
    return f"{D(x).quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"


def calcular_cune(numero, fec_ne, hor_ne, val_dev, val_ded,
                  nit, doc_trabajador, software_pin, ambiente):
    """CUNE = SHA-384 de la concatenación estricta definida en el Anexo
    Técnico (NumNE+FecNE+HorNE+ValDev+ValDed+ValTol+NitNE+DocEmp+
    TipoXML+SoftwarePIN+TipAmb)."""
    tip_amb = "1" if ambiente == "produccion" else "2"
    cadena = (str(numero) + fec_ne + hor_ne
              + _fmt2(val_dev) + _fmt2(val_ded)
              + _fmt2(D(val_dev) - D(val_ded))
              + str(nit) + str(doc_trabajador) + "102"
              + str(software_pin or "") + tip_amb)
    return hashlib.sha384(cadena.encode("utf-8")).hexdigest()


def calcular_software_security_code(software_id, software_pin, numero):
    """SHA-384(SoftwareID + SoftwarePIN + Número del documento)."""
    return hashlib.sha384(
        f"{software_id or ''}{software_pin or ''}{numero}".encode("utf-8")
    ).hexdigest()


def _sub_el(parent, tag, val):
    if val is None:
        return None
    el = ET.SubElement(parent, tag)
    el.text = str(val)
    return el


def _xml_documento(empresa, emp, tot, numero, prefijo, consecutivo, periodo,
                   ambiente, cune, cfg, nota=None):
    """Arma el XML NominaIndividual del anexo (un documento por trabajador
    por mes, con los acumulados de todas las nóminas del período)."""
    ahora = datetime.now()
    fec = ahora.strftime("%Y-%m-%d")
    hor = ahora.strftime("%H:%M:%S") + "-05:00"
    y, m = int(periodo[:4]), int(periodo[5:7])
    ultimo_dia = (datetime(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)).day
    periodo_nomina = "5" if empresa.get("periodo_pago") == "quincenal" else "4"

    root = ET.Element("NominaIndividual",
                      xmlns="dian:gov:co:facturaelectronica:NominaIndividual")

    per = ET.SubElement(root, "Periodo")
    _sub_el(per, "FechaIngreso", emp.get("fecha_ingreso"))
    _sub_el(per, "FechaRetiro", emp.get("fecha_retiro"))
    _sub_el(per, "FechaLiquidacionInicio", f"{periodo}-01")
    _sub_el(per, "FechaLiquidacionFin", f"{periodo}-{ultimo_dia:02d}")
    _sub_el(per, "TiempoLaborado", str(int(tot["dias"] or 0)))
    _sub_el(per, "FechaGeneracion", ahora.strftime("%Y-%m-%dT%H:%M:%S"))

    nseq = ET.SubElement(root, "NumeroSecuenciaXML")
    _sub_el(nseq, "CodigoTrabajador", emp.get("documento"))
    _sub_el(nseq, "Prefijo", prefijo)
    _sub_el(nseq, "Consecutivo", str(consecutivo))
    _sub_el(nseq, "Numero", numero)

    lug = ET.SubElement(root, "LugarGeneracionXML")
    _sub_el(lug, "Pais", empresa.get("pais") or "CO")
    _sub_el(lug, "DepartamentoEstado", empresa.get("departamento"))
    _sub_el(lug, "MunicipioCiudad", empresa.get("municipio"))
    _sub_el(lug, "Direccion", empresa.get("direccion"))

    gen = ET.SubElement(root, "InformacionGeneralXML")
    _sub_el(gen, "FechaGeneracion", fec)
    _sub_el(gen, "HoraGeneracion", hor)
    _sub_el(gen, "PeriodoNomina", periodo_nomina)
    _sub_el(gen, "TipoMoneda", "COP")
    _sub_el(gen, "TipoXML", "102")
    _sub_el(gen, "SoftwareSecurityCode",
            calcular_software_security_code(
                cfg.get("software_id"), cfg.get("software_pin"), numero))
    _sub_el(gen, "CUNE", cune)
    if nota:
        _sub_el(gen, "Nota", nota)

    emr = ET.SubElement(root, "EmpleadorXML")
    _sub_el(emr, "RazonSocial", empresa.get("nombre"))
    _sub_el(emr, "NIT", empresa.get("nit"))
    _sub_el(emr, "Pais", empresa.get("pais") or "CO")
    _sub_el(emr, "DepartamentoEstado", empresa.get("departamento"))
    _sub_el(emr, "MunicipioCiudad", empresa.get("municipio"))
    _sub_el(emr, "Direccion", empresa.get("direccion"))

    tr = ET.SubElement(root, "TrabajadorXML")
    _sub_el(tr, "TipoTrabajador", emp.get("tipo_trabajador") or "01")
    _sub_el(tr, "SubTipoTrabajador", emp.get("subtipo_trabajador") or "00")
    _sub_el(tr, "AltoRiesgoPension",
            "true" if emp.get("alto_riesgo_pension") else "false")
    _sub_el(tr, "TipoDocumento", emp.get("tipo_documento") or "13")
    _sub_el(tr, "NumeroDocumento", emp.get("documento"))
    _sub_el(tr, "PrimerApellido", emp.get("primer_apellido"))
    _sub_el(tr, "SegundoApellido", emp.get("segundo_apellido"))
    _sub_el(tr, "PrimerNombre", emp.get("primer_nombre"))
    _sub_el(tr, "OtrosNombres", emp.get("otros_nombres"))
    _sub_el(tr, "TipoContrato", emp.get("tipo_contrato") or "1")
    _sub_el(tr, "Sueldo", _fmt2(emp.get("salario") or 0))
    _sub_el(tr, "CodigoTrabajador", emp.get("documento"))

    pago = ET.SubElement(root, "PagoXML")
    _sub_el(pago, "Forma", "1")
    _sub_el(pago, "Metodo", "10")
    _sub_el(pago, "Banco", emp.get("banco"))
    _sub_el(pago, "TipoCuenta", emp.get("tipo_cuenta"))
    _sub_el(pago, "NumeroCuenta", emp.get("numero_cuenta"))

    fps = ET.SubElement(root, "FechasPagos")
    _sub_el(fps, "FechaPago", f"{periodo}-{ultimo_dia:02d}")

    _sub_el(root, "NumeroDiasTrabajados", str(int(tot["dias"] or 0)))

    dev = ET.SubElement(root, "DevengadosXML")
    bas = ET.SubElement(dev, "Basico")
    _sub_el(bas, "DiasTrabajados", str(int(tot["dias"] or 0)))
    _sub_el(bas, "SueldoTrabajado", _fmt2(tot["basico"] or 0))
    if D(tot["aux"] or 0) > 0:
        tra = ET.SubElement(dev, "Transporte")
        _sub_el(tra, "AuxilioTransporte", _fmt2(tot["aux"]))
    if D(tot["he"] or 0) > 0:
        hes = ET.SubElement(dev, "HorasExtras")
        he_el = ET.SubElement(hes, "HorasExtra")
        _sub_el(he_el, "Cantidad", _fmt2(tot["he"]))
        _sub_el(he_el, "Porcentaje", "25.00")
        _sub_el(he_el, "Pago", _fmt2(tot["pago_extras"]))
    if D(tot["bon"] or 0) > 0:
        bons = ET.SubElement(dev, "Bonificaciones")
        _sub_el(bons, "BonificacionS", "0.00")
        _sub_el(bons, "BonificacionNS", _fmt2(tot["bon"]))

    ded = ET.SubElement(root, "DeduccionesXML")
    sal_el = ET.SubElement(ded, "Salud")
    _sub_el(sal_el, "Porcentaje", _fmt2(tot["pct_salud"]))
    _sub_el(sal_el, "Deduccion", _fmt2(tot["salud"]))
    pen_el = ET.SubElement(ded, "FondoPension")
    _sub_el(pen_el, "Porcentaje", _fmt2(tot["pct_pension"]))
    _sub_el(pen_el, "Deduccion", _fmt2(tot["pension"]))
    fsp_el = ET.SubElement(ded, "FondoSP")
    _sub_el(fsp_el, "Porcentaje", _fmt2(tot["pct_fsp"]))
    _sub_el(fsp_el, "DeduccionSP", _fmt2(tot["fsp"]))
    if D(tot["prest"] or 0) > 0:
        lib = ET.SubElement(ded, "Libranzas")
        _sub_el(lib, "Deduccion", _fmt2(tot["prest"]))
        _sub_el(lib, "Descripcion", "Préstamo interno")
    if D(tot["ded"] or 0) > 0:
        od = ET.SubElement(ded, "OtrasDeducciones")
        _sub_el(od, "OtraDeduccion", _fmt2(tot["ded"]))

    _sub_el(root, "DevengadosTotal", _fmt2(tot["bruto"]))
    _sub_el(root, "DeduccionesTotal", _fmt2(tot["deducciones_total"]))
    _sub_el(root, "ComprobanteTotal", _fmt2(tot["neto"]))

    xml_str = ET.tostring(root, encoding="unicode")
    return xml.dom.minidom.parseString(xml_str).toprettyxml(indent="  ")


def _dian_dir(empresa_id):
    d = os.path.join(get_reports_dir(empresa_id), "dian")
    os.makedirs(d, exist_ok=True)
    return d


def _totales_periodo(conn, empresa_id, empleado_id, periodo):
    row = conn.execute("""
        SELECT SUM(dias_trabajados) dias, SUM(horas_extras) he,
               SUM(horas_extras*valor_hora_extra) pago_extras,
               SUM(bonificaciones) bon, SUM(deducciones) ded,
               SUM(salud) salud, SUM(pension) pension, SUM(fsp) fsp,
               SUM(prestamo) prest, SUM(auxilio_transporte) aux,
               SUM(salario_bruto) bruto, SUM(salario_neto) neto,
               COALESCE(SUM(ibc),0) ibc, MAX(salario_base) sb
        FROM nominas
        WHERE empresa_id=? AND empleado_id=? AND periodo=? AND quincena!=99
    """, (empresa_id, empleado_id, periodo)).fetchone()
    if not row or row["dias"] is None:
        return None
    t = dict(row)
    # El básico devengado = bruto − aux − extras − bonificaciones
    t["basico"] = (D(t["bruto"] or 0) - D(t["aux"] or 0)
                   - D(t["pago_extras"] or 0) - D(t["bon"] or 0))
    t["deducciones_total"] = (D(t["salud"] or 0) + D(t["pension"] or 0)
                              + D(t["fsp"] or 0) + D(t["ded"] or 0)
                              + D(t["prest"] or 0))
    p = parametros_vigentes(periodo)
    t["pct_salud"] = p["pct_salud"]; t["pct_pension"] = p["pct_pension"]
    t["pct_fsp"] = p["pct_fsp"]
    return t


def generar_documento_dian(empresa_id, empleado_id, periodo, regenerar=False):
    """Genera el Documento Soporte de Pago mensual de un empleado: agrega
    todas las nóminas del período (quincenas incluidas), arma el XML,
    calcula el CUNE y lo registra en documentos_dian."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        cfg = obtener_config_dian(empresa_id)
        empresa = conn.execute(
            "SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
        emp = conn.execute(
            "SELECT * FROM empleados WHERE id=? AND empresa_id=?",
            (empleado_id, empresa_id)).fetchone()
        if not empresa or not emp:
            return False, "Empresa o empleado no encontrado.", None, []
        emp = dict(emp); empresa = dict(empresa)

        prev = conn.execute(
            "SELECT id FROM documentos_dian WHERE empresa_id=? AND empleado_id=? "
            "AND periodo=? AND tipo='nomina'",
            (empresa_id, empleado_id, periodo)).fetchone()
        if prev and not regenerar:
            return False, f"Ya existe el documento del período {periodo} para este empleado.", prev["id"], []
        if prev and regenerar:
            conn.execute("DELETE FROM documentos_dian WHERE id=?", (prev["id"],))

        tot = _totales_periodo(conn, empresa_id, empleado_id, periodo)
        if not tot:
            return False, f"El empleado no tiene nóminas en el período {periodo}.", None, []

        faltantes = validar_empleado_dian(emp)
        if not empresa.get("departamento") or not empresa.get("municipio"):
            faltantes.append("Ubicación de la empresa (departamento/municipio)")

        consecutivo = int(cfg.get("consecutivo") or 0) + 1
        prefijo = cfg.get("prefijo") or "NE"
        numero = f"{prefijo}{consecutivo}"
        ambiente = cfg.get("ambiente") or "habilitacion"

        ahora = datetime.now()
        cune = calcular_cune(
            numero, ahora.strftime("%Y-%m-%d"),
            ahora.strftime("%H:%M:%S") + "-05:00",
            tot["bruto"], tot["deducciones_total"],
            empresa.get("nit"), emp.get("documento"),
            cfg.get("software_pin"), ambiente)

        xml = _xml_documento(empresa, emp, tot, numero, prefijo, consecutivo,
                             periodo, ambiente, cune, cfg)
        xml_path = os.path.join(_dian_dir(empresa_id),
                                f"{numero}_{emp.get('documento','doc')}.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml)

        conn.execute("""
            INSERT INTO documentos_dian (empresa_id, empleado_id, periodo, tipo,
                prefijo, numero, cune, xml_path, ambiente, estado)
            VALUES (?,?,?, 'nomina', ?,?,?,?,?, 'generado')
        """, (empresa_id, empleado_id, periodo, prefijo, numero, cune,
              xml_path, ambiente))
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE config_dian SET consecutivo=?, actualizado_en=datetime('now') "
            "WHERE empresa_id=?", (consecutivo, empresa_id))
        conn.commit()

        msg = f"Documento {numero} generado (CUNE: {cune[:16]}…)."
        if faltantes:
            msg += f" ⚠ Faltan datos DIAN: {', '.join(faltantes)}"
        return True, msg, doc_id, faltantes
    except Exception as e:
        return False, str(e), None, []
    finally:
        conn.close()


def generar_documentos_periodo(empresa_id, periodo, regenerar=False):
    """Genera el documento mensual para TODOS los empleados con nómina en
    el período. Devuelve (generados, omitidos, errores)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    emps = conn.execute(
        "SELECT DISTINCT empleado_id FROM nominas WHERE empresa_id=? AND periodo=?",
        (empresa_id, periodo)).fetchall()
    conn.close()
    ok_n, skip, errs = 0, [], []
    for r in emps:
        ok, msg, _doc, _warn = generar_documento_dian(
            empresa_id, r["empleado_id"], periodo, regenerar=regenerar)
        if ok:
            ok_n += 1
        else:
            (errs if "Ya existe" not in msg else skip).append(msg)
    return ok_n, skip, errs


def listar_documentos_dian(empresa_id, periodo=None):
    _verificar_scope_empresa(empresa_id)
    fil = ["d.empresa_id=?"]; params = [empresa_id]
    if periodo:
        fil.append("d.periodo=?"); params.append(periodo)
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT d.*, e.nombre as empleado_nombre, e.documento
        FROM documentos_dian d JOIN empleados e ON d.empleado_id=e.id
        WHERE {' AND '.join(fil)} ORDER BY d.id DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_documento_dian(doc_id):
    conn = get_connection()
    _verificar_scope_registro(conn, "documentos_dian", doc_id)
    row = conn.execute("""
        SELECT d.*, e.nombre as empleado_nombre, e.documento
        FROM documentos_dian d JOIN empleados e ON d.empleado_id=e.id
        WHERE d.id=?
    """, (doc_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def marcar_documento_dian(doc_id, estado, respuesta=None):
    """Actualiza el estado de transmisión del documento (manual hasta que
    exista integración con proveedor/DIAN)."""
    conn = get_connection()
    _verificar_scope_registro(conn, "documentos_dian", doc_id)
    if estado not in ("generado", "transmitido", "aceptado", "rechazado"):
        conn.close()
        return False, "Estado inválido."
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    f_envio = ahora if estado == "transmitido" else None
    f_resp = ahora if estado in ("aceptado", "rechazado") else None
    conn.execute("""
        UPDATE documentos_dian SET estado=?,
            fecha_envio=COALESCE(?, fecha_envio),
            fecha_respuesta=COALESCE(?, fecha_respuesta),
            respuesta=COALESCE(?, respuesta)
        WHERE id=?
    """, (estado, f_envio, f_resp, respuesta, doc_id))
    conn.commit()
    conn.close()
    return True, f"Documento marcado como '{estado}'."


def generar_nota_ajuste(doc_id, motivo):
    """Genera una Nota de Ajuste sobre un documento transmitido: referencia
    el CUNE original y registra el nuevo documento tipo 'ajuste'."""
    conn = get_connection()
    _verificar_scope_registro(conn, "documentos_dian", doc_id)
    try:
        orig = conn.execute(
            "SELECT * FROM documentos_dian WHERE id=?", (doc_id,)).fetchone()
        if not orig or orig["tipo"] != "nomina":
            return False, "Documento origen no encontrado.", None
        if orig["estado"] == "generado":
            return False, "El documento aún no se ha transmitido; "
            "regenérelo en lugar de crear una nota de ajuste.", None
        orig = dict(orig)
        empresa_id, empleado_id, periodo = (
            orig["empresa_id"], orig["empleado_id"], orig["periodo"])
        cfg = obtener_config_dian(empresa_id)
        empresa = dict(conn.execute(
            "SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone())
        emp = dict(conn.execute(
            "SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone())
        tot = _totales_periodo(conn, empresa_id, empleado_id, periodo)
        if not tot:
            return False, "No hay nóminas del período para ajustar.", None

        consecutivo = int(cfg.get("consecutivo") or 0) + 1
        prefijo = cfg.get("prefijo") or "NE"
        numero = f"{prefijo}{consecutivo}"
        ambiente = orig.get("ambiente") or cfg.get("ambiente")
        ahora = datetime.now()
        cune = calcular_cune(
            numero, ahora.strftime("%Y-%m-%d"),
            ahora.strftime("%H:%M:%S") + "-05:00",
            tot["bruto"], tot["deducciones_total"],
            empresa.get("nit"), emp.get("documento"),
            cfg.get("software_pin"), ambiente)

        xml = _xml_documento(empresa, emp, tot, numero, prefijo, consecutivo,
                             periodo, ambiente, cune, cfg,
                             nota=f"Nota de ajuste sobre {orig['numero']} "
                                  f"(CUNE {orig['cune'][:16]}…): {motivo}")
        xml_path = os.path.join(
            _dian_dir(empresa_id),
            f"{numero}_ajuste_{emp.get('documento','doc')}.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml)

        conn.execute("""
            INSERT INTO documentos_dian (empresa_id, empleado_id, periodo, tipo,
                prefijo, numero, cune, xml_path, ambiente, estado, doc_origen_id,
                respuesta)
            VALUES (?,?,?, 'ajuste', ?,?,?,?,?, 'generado', ?, ?)
        """, (empresa_id, empleado_id, periodo, prefijo, numero, cune,
              xml_path, ambiente, doc_id, motivo))
        nuevo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE config_dian SET consecutivo=?, actualizado_en=datetime('now') "
            "WHERE empresa_id=?", (consecutivo, empresa_id))
        conn.commit()
        return True, f"Nota de ajuste {numero} generada.", nuevo_id
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def empleados_sin_datos_dian(empresa_id):
    """Empleados activos con campos DIAN faltantes → [(nombre, [faltantes])]."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM empleados WHERE empresa_id=? AND activo=1",
        (empresa_id,)).fetchall()
    conn.close()
    res = []
    for r in rows:
        e = dict(r)
        f = validar_empleado_dian(e)
        if f:
            res.append((e["nombre"], f))
    return res


def fecha_limite_dian(periodo):
    """Fecha límite de transmisión: día 10 del mes siguiente al período."""
    y, m = int(periodo[:4]), int(periodo[5:7])
    if m == 12:
        return datetime(y + 1, 1, 10)
    return datetime(y, m + 1, 10)


def dias_plazo_dian(periodo):
    """Días restantes hasta la fecha límite de transmisión (negativo=vencido)."""
    return (fecha_limite_dian(periodo).date() - datetime.now().date()).days


def verificar_conectividad(url="https://vpfe.dian.gov.co", timeout=5):
    """Comprueba conectividad básica con la DIAN (o URL del proveedor)."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "NominaApp"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"Conectado ({r.status}) — {url}"
    except Exception as e:
        # Algunos endpoints rechazan HEAD; un error HTTP sigue siendo conexión
        if hasattr(e, "code"):
            return True, f"Conectado (HTTP {e.code}) — {url}"
        return False, f"Sin conexión a {url}: {e}"

# ─── Dinero exacto ────────────────────────────────────────────────────────────
# Toda la aritmética monetaria se hace en Decimal y se cuantiza a 2 decimales
# (centavos) con redondeo comercial antes de guardar/mostrar. Evita la deriva
# binaria de float (0.1+0.2 ≠ 0.3) en montos de nómina.
_CENT = Decimal("0.01")


def D(x) -> Decimal:
    """Convierte un monto a Decimal exacto (números, strings, None)."""
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x))


def pesos(x) -> float:
    """Cuantiza un monto a centavos (ROUND_HALF_UP) y lo devuelve como float
    listo para SQLite/formato de moneda."""
    return float(D(x).quantize(_CENT, rounding=ROUND_HALF_UP))

def _actualizar_cuota_prestamo(conn, empresa_id, empleado_id, monto_desc):
    """Descuenta monto_desc de los préstamos activos del empleado (del más antiguo al más nuevo)."""
    prestamos = conn.execute(
        "SELECT id, saldo, abonado FROM prestamos WHERE empresa_id=? AND empleado_id=? AND estado='activo' ORDER BY fecha ASC",
        (empresa_id, empleado_id)
    ).fetchall()
    restante = D(monto_desc)
    for p in prestamos:
        if restante <= 0:
            break
        saldo_p = D(p["saldo"])
        abono   = min(restante, saldo_p)
        nuevo_saldo  = saldo_p - abono
        nuevo_abonado= D(p["abonado"]) + abono
        estado = "pagado" if nuevo_saldo <= _CENT else "activo"
        conn.execute(
            "UPDATE prestamos SET abonado=?, saldo=?, estado=? WHERE id=?",
            (pesos(nuevo_abonado), pesos(max(nuevo_saldo, Decimal("0"))), estado, p["id"]))
        restante -= abono


def calcular_nomina(salario_base, dias_trabajados, horas_extras, valor_hora_extra,
                    bonificaciones, deducciones_adicionales,
                    periodo_pago='mensual', quincena=0, prestamo=0,
                    periodo=None):
    """Calcula valores de nómina con deducciones de ley sobre el IBC.

    - IBC = salario proporcional + extras + bonificaciones.
      El auxilio de transporte NO es factor salarial (no entra al IBC).
    - Salud y pensión: % parametrizado del IBC, descontado en CADA pago
      (quincena o mes) — ya no son valores fijos.
    - FSP (Fondo de Solidaridad Pensional): % del IBC cuando el salario
      supera el umbral de SMMLV parametrizado (por defecto 4 SMMLV).
    - Auxilio de transporte: prorrateado por días trabajados, solo si el
      salario no supera 2 SMMLV.
    `periodo` ('YYYY-MM') selecciona los parámetros legales del año vigente;
    los cálculos históricos se reproducen con los valores de su año.
    """
    p = parametros_vigentes(periodo)
    smmlv        = D(p["smmlv"])
    aux_mes      = D(p["aux_transporte"])
    pct_salud    = D(p["pct_salud"]) / 100
    pct_pension  = D(p["pct_pension"]) / 100
    pct_fsp      = D(p["pct_fsp"]) / 100
    umbral_fsp   = D(p["umbral_fsp_smmlv"])

    sb = D(salario_base)
    salario_dia = sb / 30

    salario_proporcional = salario_dia * D(dias_trabajados)
    valor_horas_extras   = D(horas_extras) * D(valor_hora_extra)
    ibc                  = salario_proporcional + valor_horas_extras + D(bonificaciones)
    aux_transporte       = (aux_mes / 30 * D(dias_trabajados)
                            if sb <= smmlv * 2 else Decimal("0"))
    salario_bruto        = ibc + aux_transporte

    salud   = ibc * pct_salud
    pension = ibc * pct_pension
    fsp     = ibc * pct_fsp if sb >= smmlv * umbral_fsp else Decimal("0")

    salario_neto = (salario_bruto - salud - pension - fsp
                    - D(deducciones_adicionales) - D(prestamo))

    return {
        "salario_bruto":   pesos(salario_bruto),
        "aux_transporte":  pesos(aux_transporte),
        "ibc":             pesos(ibc),
        "salud":           pesos(salud),
        "pension":         pesos(pension),
        "fsp":             pesos(fsp),
        "prestamo":        pesos(prestamo),
        "salario_neto":    pesos(salario_neto),
        "deducciones_ley": pesos(salud + pension + fsp),
        "dias_base":       15 if periodo_pago == 'quincenal' else 30,
    }


def validar_salario_minimo(salario, periodo=None):
    """Retorna (ok, mensaje) indicando si el salario está por debajo del
    SMMLV vigente para el período (o el año actual)."""
    smmlv = D(parametros_vigentes(periodo)["smmlv"])
    if D(salario) < smmlv:
        return False, (f"⚠ El salario ingresado ($ {salario:,.0f}) está por debajo del "
                       f"salario mínimo legal vigente ($ {smmlv:,.0f}).")
    return True, ""


# ─── PRÉSTAMOS ────────────────────────────────────────────────────────────────

def crear_prestamo(empresa_id, empleado_id, monto, fecha, observaciones=""):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO prestamos (empresa_id, empleado_id, monto, saldo, fecha, observaciones) VALUES (?,?,?,?,?,?)",
            (empresa_id, empleado_id, pesos(monto), pesos(monto), fecha, observaciones)
        )
        conn.commit()
        return True, "Préstamo registrado exitosamente."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def listar_prestamos(empresa_id, empleado_id=None, estado=None):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtros = ["p.empresa_id=?"]
    params = [empresa_id]
    if empleado_id:
        filtros.append("p.empleado_id=?")
        params.append(empleado_id)
    if estado:
        filtros.append("p.estado=?")
        params.append(estado)
    where = " AND ".join(filtros)
    rows = conn.execute(f"""
        SELECT p.*, e.nombre as empleado_nombre, e.documento, e.cargo
        FROM prestamos p JOIN empleados e ON p.empleado_id = e.id
        WHERE {where}
        ORDER BY p.fecha DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_prestamo_activo(empresa_id, empleado_id):
    """Obtiene el préstamo activo de un empleado (saldo pendiente)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM prestamos WHERE empresa_id=? AND empleado_id=? AND estado='activo' LIMIT 1",
        (empresa_id, empleado_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def actualizar_prestamo(prestamo_id, monto, observaciones=""):
    conn = get_connection()
    _verificar_scope_registro(conn, "prestamos", prestamo_id)
    try:
        conn.execute(
            "UPDATE prestamos SET monto=?, saldo=monto-abonado, observaciones=? WHERE id=?",
            (pesos(monto), observaciones, prestamo_id)
        )
        conn.commit()
        return True, "Préstamo actualizado."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def abonar_prestamo(prestamo_id, monto_abono):
    """Registra un abono a un préstamo. Si el saldo llega a 0, marca como pagado."""
    conn = get_connection()
    _verificar_scope_registro(conn, "prestamos", prestamo_id)
    try:
        row = conn.execute("SELECT saldo FROM prestamos WHERE id=?", (prestamo_id,)).fetchone()
        if not row:
            return False, "Préstamo no encontrado."
        saldo_actual = D(row["saldo"])
        if D(monto_abono) > saldo_actual:
            return False, f"El abono (${monto_abono:,.0f}) supera el saldo pendiente (${saldo_actual:,.0f})."
        nuevo_abonado = D(conn.execute("SELECT abonado FROM prestamos WHERE id=?", (prestamo_id,)).fetchone()["abonado"]) + D(monto_abono)
        nuevo_saldo = saldo_actual - D(monto_abono)
        estado = "pagado" if nuevo_saldo <= 0 else "activo"
        conn.execute(
            "UPDATE prestamos SET abonado=?, saldo=?, estado=? WHERE id=?",
            (pesos(nuevo_abonado), pesos(nuevo_saldo), estado, prestamo_id)
        )
        conn.commit()
        return True, f"Abono registrado. Saldo pendiente: ${nuevo_saldo:,.0f}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def eliminar_prestamo(prestamo_id):
    conn = get_connection()
    _verificar_scope_registro(conn, "prestamos", prestamo_id)
    conn.execute("DELETE FROM prestamos WHERE id=?", (prestamo_id,))
    conn.commit()
    conn.close()
    return True, "Préstamo eliminado."


# ─── CONTRATOS ────────────────────────────────────────────────────────────────

def crear_contrato(empresa_id, empleado_id, descripcion, valor_total,
                   fecha_inicio, fecha_fin=None, deducciones=0, observaciones=None):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO contratos (empresa_id, empleado_id, descripcion, valor_total,
                fecha_inicio, fecha_fin, deducciones, observaciones, estado)
            VALUES (?,?,?,?,?,?,?,?,'activo')
        """, (empresa_id, empleado_id, descripcion, pesos(valor_total),
              fecha_inicio, fecha_fin, pesos(deducciones), observaciones))
        conn.commit()
        return True, "Contrato creado exitosamente."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def listar_contratos(empresa_id, estado=None):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    filtro = "AND c.estado=?" if estado else ""
    params = [empresa_id] + ([estado] if estado else [])
    rows = conn.execute(f"""
        SELECT c.*, e.nombre as empleado_nombre, e.documento, e.cargo
        FROM contratos c
        JOIN empleados e ON c.empleado_id = e.id
        WHERE c.empresa_id=? {filtro}
        ORDER BY c.creado_en DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_contrato(contrato_id, empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    row = conn.execute("""
        SELECT c.*, e.nombre as empleado_nombre, e.documento, e.cargo,
               e.fecha_ingreso, e.tipo_cuenta, e.numero_cuenta,
               em.nombre as empresa_nombre, em.nit, em.direccion, em.telefono, em.logo_path
        FROM contratos c
        JOIN empleados e ON c.empleado_id = e.id
        JOIN empresas em ON c.empresa_id = em.id
        WHERE c.id=? AND c.empresa_id=?
    """, (contrato_id, empresa_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def actualizar_contrato(contrato_id, empresa_id, descripcion, valor_total,
                        fecha_inicio, fecha_fin=None, deducciones=0, observaciones=None):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE contratos SET descripcion=?, valor_total=?, fecha_inicio=?,
                fecha_fin=?, deducciones=?, observaciones=?
            WHERE id=? AND empresa_id=? AND estado='activo'
        """, (descripcion, pesos(valor_total), fecha_inicio, fecha_fin,
              pesos(deducciones), observaciones, contrato_id, empresa_id))
        conn.commit()
        return True, "Contrato actualizado."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def liquidar_contrato(contrato_id, empresa_id, valor_final=None,
                      deducciones_final=None, observaciones_liq=None,
                      descontar_prestamos=False):
    """Liquida el contrato: genera nómina especial, guarda colilla, marca como liquidado.
    Si descontar_prestamos=True, descuenta el saldo total de préstamos activos del empleado."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        cont = conn.execute(
            "SELECT * FROM contratos WHERE id=? AND empresa_id=?",
            (contrato_id, empresa_id)).fetchone()
        if not cont:
            return False, "Contrato no encontrado.", None
        if cont["estado"] == "liquidado":
            return False, "Este contrato ya fue liquidado.", None

        val   = D(valor_final)   if valor_final   is not None else D(cont["valor_total"])
        ded   = D(deducciones_final) if deducciones_final is not None else D(cont["deducciones"])

        # Manejar préstamos activos
        prestamo_desc = Decimal("0")
        if descontar_prestamos:
            prestamos = conn.execute(
                "SELECT id, saldo FROM prestamos WHERE empresa_id=? AND empleado_id=? AND estado='activo'",
                (empresa_id, cont["empleado_id"])).fetchall()
            for p in prestamos:
                prestamo_desc += D(p["saldo"])
                # Marcar préstamo como pagado
                conn.execute(
                    "UPDATE prestamos SET abonado=monto, saldo=0, estado='pagado' WHERE id=?",
                    (p["id"],))

        neto  = val - ded - prestamo_desc
        obs   = observaciones_liq or f"Liquidación contrato: {cont['descripcion']}"
        if prestamo_desc > 0:
            obs += f" | Préstamos descontados: {prestamo_desc:,.0f}"
        periodo = datetime.now().strftime("%Y-%m")

        conn.execute("""
            INSERT INTO nominas (empresa_id, empleado_id, periodo, quincena,
                dias_trabajados, horas_extras, valor_hora_extra, bonificaciones,
                deducciones, salud, pension, prestamo, salario_base,
                salario_bruto, salario_neto, observaciones)
            VALUES (?,?,?,99, 0,0,0,0, ?,0,0,?, ?,?,?,?)
        """, (empresa_id, cont["empleado_id"], periodo,
              pesos(ded), pesos(prestamo_desc), pesos(val), pesos(val), pesos(neto), obs))
        nomina_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute("""
            UPDATE contratos SET estado='liquidado', nomina_id=?,
                valor_total=?, deducciones=?, observaciones=?
            WHERE id=?
        """, (nomina_id, pesos(val), pesos(ded + prestamo_desc), obs, contrato_id))
        conn.commit()
        return True, "Contrato liquidado exitosamente.", nomina_id
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def eliminar_contrato(contrato_id, empresa_id):
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    try:
        cont = conn.execute(
            "SELECT estado FROM contratos WHERE id=? AND empresa_id=?",
            (contrato_id, empresa_id)).fetchone()
        if cont and cont["estado"] == "liquidado":
            return False, "No se puede eliminar un contrato ya liquidado."
        conn.execute("DELETE FROM contratos WHERE id=? AND empresa_id=?",
                     (contrato_id, empresa_id))
        conn.commit()
        return True, "Contrato eliminado."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def obtener_prestamos_activos(empresa_id, empleado_id):
    """Retorna TODOS los préstamos activos de un empleado (permite múltiples)."""
    _verificar_scope_empresa(empresa_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM prestamos WHERE empresa_id=? AND empleado_id=? AND estado='activo' ORDER BY fecha DESC",
        (empresa_id, empleado_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── EXPORTAR EXCEL ──────────────────────────────────────────────────────────

def exportar_nomina_excel(empresa_id, periodo, ruta_destino):
    """Exporta la nómina de un período a un archivo Excel (.xlsx)."""
    _verificar_scope_empresa(empresa_id)
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    nominas = listar_nominas(empresa_id, periodo)
    empresa = None
    conn = get_connection()
    row = conn.execute("SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
    conn.close()
    if row:
        empresa = dict(row)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Nómina {periodo}"

    # Estilos
    navy = "0F172A"
    indigo = "4F46E5"
    emerald = "10B981"

    hdr_font   = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    title_font = Font(name="Calibri", bold=True, color="FFFFFF", size=14)
    body_font  = Font(name="Calibri", size=10)
    total_font = Font(name="Calibri", bold=True, color=indigo, size=11)
    neto_font  = Font(name="Calibri", bold=True, color=emerald, size=11)

    navy_fill   = PatternFill("solid", fgColor=navy)
    indigo_fill = PatternFill("solid", fgColor=indigo)
    silver_fill = PatternFill("solid", fgColor="E2E8F0")
    white_fill  = PatternFill("solid", fgColor="FFFFFF")

    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    left   = Alignment(horizontal="left",   vertical="center")
    right  = Alignment(horizontal="right",  vertical="center")

    # Fila 1 — título empresa
    nom_empresa = empresa["nombre"].upper() if empresa else "EMPRESA"
    ws.merge_cells("A1:M1")
    ws["A1"] = nom_empresa
    ws["A1"].font = title_font
    ws["A1"].fill = navy_fill
    ws["A1"].alignment = center

    # Fila 2 — NIT y período
    ws.merge_cells("A2:M2")
    nit = empresa.get("nit","") if empresa else ""
    ws["A2"] = f"NIT: {nit}  |  Período: {periodo}  |  Reporte de Nómina"
    ws["A2"].font = Font(name="Calibri", bold=True, color="C7D2FE", size=10)
    ws["A2"].fill = indigo_fill
    ws["A2"].alignment = center

    # Fila 3 — vacía
    ws.row_dimensions[3].height = 6

    # Encabezados columnas
    cols = [
        ("#", 5), ("Empleado", 28), ("Documento", 14), ("Cargo", 20),
        ("Días", 7), ("H. Extras", 9), ("Bonif.", 13), ("Otras Deds.", 13),
        ("Préstamo", 13), ("Salario Base", 15), ("Bruto", 15),
        ("Salud+Pensión", 15), ("Neto a Pagar", 15),
    ]
    for col_idx, (header, width) in enumerate(cols, 1):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = hdr_font
        cell.fill = navy_fill
        cell.alignment = center
        cell.border = border
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[4].height = 22

    # Datos
    total_bruto = total_neto = total_deds = 0
    for i, n in enumerate(nominas, 1):
        row_num = i + 4
        ded_total = (float(n.get("salud",0)) + float(n.get("pension",0)) +
                     float(n.get("prestamo",0)) + float(n.get("deducciones",0)))
        total_bruto += float(n.get("salario_bruto", 0))
        total_neto  += float(n.get("salario_neto", 0))
        total_deds  += ded_total
        fill = white_fill if i % 2 == 0 else silver_fill
        vals = [
            i,
            n.get("empleado_nombre",""),
            n.get("documento",""),
            n.get("cargo",""),
            n.get("dias_trabajados", 30),
            n.get("horas_extras", 0),
            float(n.get("bonificaciones",0)),
            float(n.get("deducciones",0)),
            float(n.get("prestamo",0)),
            float(n.get("salario_base",0)),
            float(n.get("salario_bruto",0)),
            float(n.get("salud",0)) + float(n.get("pension",0)),
            float(n.get("salario_neto",0)),
        ]
        alignments = [center, left, center, left, center, center,
                      right, right, right, right, right, right, right]
        for col_idx, (val, aln) in enumerate(zip(vals, alignments), 1):
            cell = ws.cell(row=row_num, column=col_idx, value=val)
            cell.font = body_font
            cell.fill = fill
            cell.alignment = aln
            cell.border = border
            # Formato moneda para columnas de valores
            if col_idx >= 7:
                cell.number_format = '#,##0'
        ws.row_dimensions[row_num].height = 18

    # Fila de totales
    total_row = len(nominas) + 5
    ws.merge_cells(f"A{total_row}:E{total_row}")
    tc = ws.cell(row=total_row, column=1, value="TOTALES")
    tc.font = total_font
    tc.fill = PatternFill("solid", fgColor="EEF2FF")
    tc.alignment = center
    tc.border = border

    for col_idx, val in [(11, total_bruto), (12, total_deds), (13, total_neto)]:
        cell = ws.cell(row=total_row, column=col_idx, value=val)
        cell.font = neto_font if col_idx == 13 else total_font
        cell.fill = PatternFill("solid", fgColor="EEF2FF")
        cell.alignment = right
        cell.border = border
        cell.number_format = '#,##0'
    for col_idx in [6, 7, 8, 9, 10]:
        ws.cell(row=total_row, column=col_idx).border = border
    ws.row_dimensions[total_row].height = 22

    # Fijar primera fila de encabezados
    ws.freeze_panes = "A5"

    wb.save(ruta_destino)
    return ruta_destino


def registrar_colilla_generada(nomina_id, colilla_path):
    """Guarda la ruta de la colilla PDF en el registro de nómina."""
    conn = get_connection()
    _verificar_scope_registro(conn, "nominas", nomina_id)
    conn.execute("UPDATE nominas SET colilla_path=? WHERE id=?", (colilla_path, nomina_id))
    conn.commit()
    conn.close()


def obtener_colilla_path(nomina_id):
    """Retorna la ruta de la colilla si ya fue generada, o None."""
    conn = get_connection()
    _verificar_scope_registro(conn, "nominas", nomina_id)
    row = conn.execute("SELECT colilla_path FROM nominas WHERE id=?", (nomina_id,)).fetchone()
    conn.close()
    if row and row["colilla_path"] and os.path.exists(row["colilla_path"]):
        return row["colilla_path"]
    return None


def smtp_config_for_email(correo):
    """Detecta servidor SMTP, puerto y URL webmail según el dominio."""
    if not correo: return None, None, None
    dominio = correo.split("@")[-1].lower()
    configs = {
        "gmail.com":     ("smtp.gmail.com",       587, "https://mail.google.com"),
        "googlemail.com":("smtp.gmail.com",       587, "https://mail.google.com"),
        "hotmail.com":   ("smtp.office365.com",   587, "https://outlook.live.com"),
        "outlook.com":   ("smtp.office365.com",   587, "https://outlook.live.com"),
        "live.com":      ("smtp.office365.com",   587, "https://outlook.live.com"),
        "live.com.co":   ("smtp.office365.com",   587, "https://outlook.live.com"),
        "msn.com":       ("smtp.office365.com",   587, "https://outlook.live.com"),
        "yahoo.com":     ("smtp.mail.yahoo.com",  587, "https://mail.yahoo.com"),
        "yahoo.es":      ("smtp.mail.yahoo.com",  587, "https://mail.yahoo.com"),
        "yahoo.com.co":  ("smtp.mail.yahoo.com",  587, "https://mail.yahoo.com"),
        "icloud.com":    ("smtp.mail.me.com",     587, "https://www.icloud.com/mail"),
        "me.com":        ("smtp.mail.me.com",     587, "https://www.icloud.com/mail"),
    }
    return configs.get(dominio, ("smtp." + dominio, 587, f"https://mail.{dominio}"))


def _instrucciones_clave_app(dominio):
    """Retorna instrucciones específicas para crear contraseña de aplicación."""
    if "gmail" in dominio or "google" in dominio:
        return (
            "Gmail requiere una 'Contraseña de aplicación':\n\n"
            "1. Ve a myaccount.google.com\n"
            "2. Seguridad → Verificación en 2 pasos (actívala)\n"
            "3. Busca 'Contraseñas de aplicaciones'\n"
            "4. Selecciona 'Correo' y genera la clave\n"
            "5. Copia los 16 caracteres SIN espacios y úsalos aquí\n\n"
            "⚠ NO uses tu contraseña normal de Gmail."
        )
    elif any(d in dominio for d in ["hotmail","outlook","live","msn"]):
        return (
            "Outlook/Hotmail personal ya NO soporta contraseñas de aplicación.\n\n"
            "Opciones:\n"
            "① Usa una cuenta Gmail con contraseña de aplicación (recomendado)\n"
            "② Activa SMTP en Outlook: outlook.live.com → Configuración\n"
            "   → Correo → Sincronizar correo → POP e IMAP → Activar SMTP\n"
            "   Luego usa tu contraseña normal de Outlook.\n\n"
            "⚠ Las cuentas Microsoft personales tienen restricciones SMTP."
        )
    return "Verifica que el correo y la contraseña de aplicación sean correctos."


def enviar_colilla_email(empresa, colilla_path, destino, nombre_empleado, periodo, neto):
    """Envía la colilla de pago usando las credenciales SMTP de la empresa."""
    if empresa.get("id"):
        _verificar_scope_empresa(empresa["id"])
    import smtplib, ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    correo  = (empresa.get("correo","") or "").strip()
    password= descifrar_secreto(empresa.get("correo_password","") or "").strip()

    if not correo:
        return False, "La empresa no tiene correo configurado.\nPide al SuperAdmin que lo configure en 'Ajustes de empresa'."
    if not password:
        return False, "La empresa no tiene contraseña de correo configurada.\nPide al SuperAdmin que la configure."
    if not destino or "@" not in destino:
        return False, "El correo del destinatario no es válido."

    smtp_srv, smtp_port, _ = smtp_config_for_email(correo)
    dominio = correo.split("@")[-1].lower()

    # Construir mensaje
    msg = MIMEMultipart()
    msg["From"]    = correo
    msg["To"]      = destino
    msg["Subject"] = f"Colilla de pago — {nombre_empleado} — Período {periodo}"
    cuerpo = (
        f"Estimado(a) {nombre_empleado},\n\n"
        f"Adjunto encontrará su colilla de pago del período {periodo}.\n\n"
        f"Empresa: {empresa.get('nombre','')}\n"
        f"Neto a pagar: {neto}\n\n"
        f"Saludos cordiales,\n{empresa.get('nombre','')}"
    )
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    with open(colilla_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
        f"attachment; filename={os.path.basename(colilla_path)}")
    msg.attach(part)

    # Intentar envío — primero STARTTLS (587), fallback SSL (465)
    ultimo_error = None
    intentos = [
        (smtp_srv, 587, "STARTTLS"),
        (smtp_srv, 465, "SSL"),
    ]
    for srv, port, modo in intentos:
        try:
            if modo == "SSL":
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(srv, port, context=ctx, timeout=15) as server:
                    server.login(correo, password)
                    server.sendmail(correo, destino, msg.as_string())
            else:
                with smtplib.SMTP(srv, port, timeout=15) as server:
                    server.ehlo(); server.starttls(); server.ehlo()
                    server.login(correo, password)
                    server.sendmail(correo, destino, msg.as_string())
            return True, f"✓ Colilla enviada exitosamente a {destino}"
        except smtplib.SMTPAuthenticationError:
            instrucciones = _instrucciones_clave_app(dominio)
            return False, f"Error de autenticación ({modo}).\n\n{instrucciones}"
        except smtplib.SMTPException as ex:
            ultimo_error = f"{modo}:{port} — {ex}"
            continue
        except OSError as ex:
            ultimo_error = f"{modo}:{port} — {ex}"
            continue

    return False, (
        f"No se pudo conectar al servidor de correo.\n\n"
        f"Servidor: {smtp_srv}\n"
        f"Último error: {ultimo_error}\n\n"
        f"Verifica que tengas conexión a internet y que el correo esté bien configurado."
    )