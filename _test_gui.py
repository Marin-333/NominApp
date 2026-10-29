"""Smoke test de GUI: instancia las ventanas y construye cada sección."""
import os, sys, tempfile, traceback

TMP = tempfile.mkdtemp(prefix="nomina_gui_test_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths
paths.APP_DIR = TMP
paths.DB_PATH = os.path.join(TMP, "nomina.db")
paths.REPORTS_DIR = os.path.join(TMP, "reports")
os.makedirs(paths.REPORTS_DIR, exist_ok=True)

import database as db
db.DB_PATH = paths.DB_PATH
db.BACKUP_DIR = os.path.join(TMP, "backups")
db.init_db(); db.migrar_base_datos()
db.establecer_sesion(db.autenticar("superadmin", "Admin2024!"))  # Fase 3

db.crear_empresa("Test SA", "900111", "Calle 1", "3001234", periodo_pago="quincenal")
EID = db.listar_empresas()[0]["id"]
db.crear_admin("admin1", "Pass1234", "Admin Uno", EID)
adm = db.autenticar("admin1", "Pass1234")
db.crear_empleado(EID, "Juan P", "1001", "Op", 2000000, "2024-01-15")
db.crear_empleado(EID, "Ana P", "2002", "Cons", 150000, "2024-03-01", None, None, 30, "prestacion", 150000)
db.crear_prestamo(EID, db.listar_empleados(EID)[0]["id"], 100000, "2026-09-01", "t")

import app

results = []
def sec(win, name):
    try:
        win._sec(name)
        win.update_idletasks()
        results.append((name, "OK"))
    except Exception as e:
        results.append((name, f"FAIL: {e}"))
        traceback.print_exc()

# AdminWin — todas las secciones
w = app.AdminWin(adm)
w.update_idletasks()
for s in ["dash","emps","prestamos","novedades","contratos","nomina","colilla","hist","dian","rep","bitacora","backup"]:
    sec(w, s)
w.destroy()

# SuperAdminWin
w2 = app.SuperAdminWin({"id":1,"nombre":"SA","rol":"superadmin"})
w2.update_idletasks()
for s in ["empresas","admins","bitacora","parametros","backup"]:
    sec(w2, s)
w2.destroy()

print("\n=== RESULTADOS GUI ===")
for n, r in results:
    print(f"  [{r.split(':')[0]:>4}] {n} {'' if r=='OK' else r}")
fails = [n for n,r in results if r != "OK"]
print(f"{len(fails)} secciones con fallo" + (f": {fails}" if fails else ""))
