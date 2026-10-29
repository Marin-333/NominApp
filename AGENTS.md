# NominaApp - Notas del proyecto

## Flujo de trabajo obligatorio (regla del usuario)

Siempre que se pida un cambio:
1. Hacer el cambio.
2. Revisar errores (parse + pyflakes + tests).
3. Corregir los fallos que aparezcan.
4. Exportar/recompilar la app (`dist/app.exe`).

## Comandos de verificación

```bash
# Sintaxis + análisis estático
python -c "import ast; [ast.parse(open(f,encoding='utf8').read()) for f in ['app.py','database.py','pdf_generator.py','paths.py']]"
python -m pyflakes app.py database.py pdf_generator.py paths.py

# Suite de datos/PDF (usa BD temporal, no toca nomina.db)
python _test_all.py

# Smoke test GUI (construye todas las secciones)
python _test_gui.py
```

## Build

```bash
python -m PyInstaller app.spec --clean --noconfirm
# Resultado: dist/app.exe  (onefile, sin consola)
```

Nota: `build.bat` apunta a `NominaApp.spec` (genera `NominaApp.exe`),
pero el ejecutable en uso es `app.exe` producido por `app.spec`.
Hay 3 specs en el repo (`app.spec`, `NominaApp.spec`, `nomina_app.spec`)
pendientes de consolidar.

## Estructura

- `app.py` — GUI Tkinter
- `database.py` — SQLite + lógica de negocio (nómina, préstamos, contratos, backups)
- `_sqlcipher.py` — precarga `sqlite_cipher/sqlite3.dll` antes de `import sqlite3`
- `sqlite_cipher/sqlite3.dll` — e_sqlcipher.dll renombrada (NuGet
  `SQLitePCLRaw.lib.e_sqlcipher` 2.1.11; SQLCipher community, Apache-2.0/BSD).
  Va en `app.spec` → `binaries`. Si falta, la app corre en texto plano.
- `pdf_generator.py` — reportes ReportLab
- `paths.py` — rutas persistentes (DB y reports junto al exe cuando está congelado)
- `nomina.db` + `nomina.db.key`, `reports/`, `logos/`, `fotos/` — datos persistentes

## Advertencias conocidas

- El backup de empresa remapea IDs al importar (no insertar IDs originales).
- La FK `empleados` tiene `ON DELETE CASCADE`: "mantener nóminas" = inactivar.
- `nomina.db` está cifrada (SQLCipher). La llave está en `nomina.db.key`
  protegida con DPAPI del usuario de Windows: sin ese archivo (o en otro
  equipo/perfil) la BD no abre. Para backups portables, exportar CON
  contraseña desde la sección Backup.
- Contraseñas de usuarios: bcrypt (migración transparente desde SHA-256).
- Las funciones de `database.py` validan sesión/rol/empresa: en scripts de
  prueba llamar primero `db.establecer_sesion(usuario)` o saltará PermissionError.
