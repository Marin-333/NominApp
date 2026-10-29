"""Genera todos los PDFs con datos de prueba y los renderiza a PNG para inspección visual.
Uso: python _test_pdf_visual.py  -> imprime PNGDIR=... al final."""
import os, sys, tempfile

TMP = tempfile.mkdtemp(prefix='nomina_pdfs_')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
paths.DB_PATH = os.path.join(TMP, 't.db')
paths.REPORTS_DIR = os.path.join(TMP, 'reports')
os.makedirs(paths.REPORTS_DIR, exist_ok=True)
import database as db
db.DB_PATH = paths.DB_PATH
import pdf_generator as pdf
def _rd(eid=None):
    if eid:
        d = os.path.join(paths.REPORTS_DIR, f'empresa_{eid}')
        os.makedirs(d, exist_ok=True)
        return d
    return paths.REPORTS_DIR
pdf.get_reports_dir = _rd

db.init_db(); db.migrar_base_datos()
u = db.autenticar('superadmin', 'Admin2024!')
db.establecer_sesion(u)
db.crear_empresa('Empresa Demo S.A.S.', '900123456-7', 'demo@demo.com', 'Cra 45 #12-30', '6045551234', 'quincenal')
EID = db.listar_empresas()[0]['id']
db.crear_empleado(EID, 'María Fernanda González Rodríguez', '1045789123', 'Contadora Senior', 2850000, '2021-03-15',
                  tipo_cuenta='Ahorros', numero_cuenta='4567890123456', edad=34, tipo_vinculacion='salario', correo='maria@demo.com')
db.crear_empleado(EID, 'Juan Carlos Pérez Ramírez', '79845321', 'Analista de Sistemas', 1800000, '2023-06-01', edad=28, tipo_vinculacion='salario')
db.crear_empleado(EID, 'Ana María López Cárdenas', '1122334455', 'Consultora Externa', 0, '2024-01-10', edad=31, tipo_vinculacion='prestacion', tarifa_diaria=120000)
emps = {e['documento']: e['id'] for e in db.listar_empleados(EID)}
MARIA, JUAN, ANA = emps['1045789123'], emps['79845321'], emps['1122334455']

db.guardar_nomina(EID, MARIA, '2026-09', 15, 2.5, 35000, 150000, 50000, 'Observación larga para probar textos largos en la colilla de pago', 2850000, quincena=1, prestamo=0, periodo_pago='quincenal')
db.guardar_nomina(EID, MARIA, '2026-09', 15, 0, 0, 0, 0, '', 2850000, quincena=2, prestamo=0, periodo_pago='quincenal')
db.guardar_nomina(EID, JUAN, '2026-09', 15, 1.0, 22000, 0, 0, '', 1800000, quincena=1, periodo_pago='quincenal')
db.crear_prestamo(EID, MARIA, 600000, '2026-09-01', 'Compra equipo')
ok, msg, nid = db.guardar_nomina_prestacion(EID, ANA, '2026-09', 18, 'Contrato de prestación de servicios profesionales')
ok, msg = db.crear_contrato(EID, ANA, 'Desarrollo módulo contable', 4800000, '2026-07-01', '2026-09-30')
CID = db.listar_contratos(EID)[0]['id']
db.liquidar_contrato(CID, EID)
db.generar_prestacion(EID, 'prima', '2026-1S')
db.registrar_bitacora(1, 'admin', 'Generación nómina', '2026-09 quincena 1', empresa_id=EID)

conn = db.get_connection()
empresa = dict(conn.execute('SELECT * FROM empresas WHERE id=?', (EID,)).fetchone())
conn.close()

noms = db.listar_nominas(EID)
outs = []
def _try(name, fn, *a, **k):
    try:
        r = fn(*a, **k)
        if r: outs.append(r)
        else: print(f'PDF {name}: None')
    except Exception as ex:
        print(f'PDF {name} ERROR: {ex}')
for q in (1, 2, 98, 99):
    ns = [n for n in noms if n['quincena'] == q]
    if ns:
        _try(f'colilla_q{q}', pdf.generar_colilla,
             db.obtener_nomina(ns[0]['id'], EID), empresa_id=EID)
_try('reporte_nomina', pdf.generar_reporte_nomina, empresa, noms, '2026-09', empresa_id=EID)
_try('reporte_empleados', pdf.generar_reporte_empleados, db.listar_empleados(EID), empresa, empresa_id=EID)
_try('reporte_bitacora', pdf.generar_reporte_bitacora, db.listar_bitacora(EID), empresa, EID)
_try('bitacora_global', pdf.generar_reporte_bitacora, db.listar_bitacora())
_try('certificado_maria', pdf.generar_certificado_laboral, dict(db.obtener_empleado(MARIA, EID)), empresa, EID)
_try('certificado_ana', pdf.generar_certificado_laboral, dict(db.obtener_empleado(ANA, EID)), empresa, EID)

try:
    xl = db.exportar_nomina_excel(EID, '2026-09', os.path.join(TMP, 'nomina.xlsx'))
    print('EXCEL:', xl)
except Exception as ex:
    print('EXCEL ERROR:', ex)

import pymupdf
pngdir = os.path.join(TMP, 'png')
os.makedirs(pngdir)
i = 0
for p in outs:
    d = pymupdf.open(p)
    for pi in range(len(d)):
        pix = d[pi].get_pixmap(dpi=110)
        out = os.path.join(pngdir, f'{i:02d}_{os.path.basename(p)[:-4]}_p{pi+1}.png')
        pix.save(out)
        print(os.path.basename(out))
        i += 1
print('PNGDIR=' + pngdir)
