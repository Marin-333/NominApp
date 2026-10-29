# NominaApp — Plan de Seguridad y Cumplimiento

Checklist consolidado de endurecimiento (security hardening), estabilidad de
despliegue y cumplimiento DIAN (nómina electrónica) + Habeas Data.

> Estado: se marca `[x]` solo cuando el ítem está implementado **y verificado**
> (`_test_all.py` + `_test_gui.py` + rebuild de `dist/app.exe`).

---

## Fase 0 — Higiene base  ✅ COMPLETADA (2026-10-29)

- [x] `.gitignore`: excluir `nomina.db`, `reports/`, `logos/`, `fotos/`, `dist/`, `build/`
- [x] Mutex de instancia única — impide dos `app.exe` simultáneos (corrupción SQLite)
- [x] `report_callback_exception` + `sys.excepthook` → log a archivo rotativo + `messagebox` amigable
- [x] Backup automático de `nomina.db` al arrancar + `PRAGMA integrity_check` antes de migrar
- [x] Tabla `meta` con versión de app y de esquema (migraciones seguras)
- [x] Decisión documentada: datos junto al .exe (modo portable). Riesgo conocido: si el exe se mueve a `Program Files`, requiere permisos de admin.

## Fase 1 — Autenticación  ✅ COMPLETADA (2026-10-29)

- [x] Migrar SHA-256 sin salt → **bcrypt**, con migración transparente al login (detecta hash de 64 hex → verifica → rehashea). `database.py: hash_password/verify_password`
- [x] Forzar cambio del password por defecto `superadmin`/`Admin2024!` — flag `debe_cambiar_password` + modal obligatorio al login (`_forzar_cambio_password`)
- [x] Lockout: 5 intentos fallidos → bloqueo 15 min (`intentos_fallidos`, `bloqueado_hasta`); intentos, bloqueos y logins quedan en bitácora
- [x] Política de contraseñas: mínimo 8 chars + letra + número (`politica_password_ok` en BD + `val_pass` en GUI)
- [x] `empresas.correo_password` cifrado en reposo con **DPAPI** (CryptProtectData, ligado al usuario de Windows — sin llave que gestionar); `cifrar_secreto`/`descifrar_secreto`
- [x] Auto-logout por inactividad (15 min, `SESION_MINUTOS` en `BaseWin`) + re-autenticación `_reautenticar()` en: eliminación definitiva de empleado, restaurar backup de empresa y restaurar backup completo

## Fase 2 — Datos en reposo ✅ (completada)

- [x] **SQLCipher para la BD completa**: `sqlite_cipher/sqlite3.dll` (e_sqlcipher de SQLitePCLRaw.lib.e_sqlcipher 2.1.11, renombrada) se precarga con ctypes en `_sqlcipher.py` antes de `import sqlite3` — el módulo estándar queda enlazado a SQLCipher y `PRAGMA key` funciona sin cambiar el API. Nota: `sqlcipher3-binary` no publica wheels Windows → se optó por la DLL empaquetada en `app.spec`
- [x] Migración automática en `init_db` → `asegurar_cifrado()`: BD en claro se convierte con `sqlcipher_export` + integrity_check antes de reemplazar; backups viejos en `backups/` también se cifran
- [x] Gestión de llave vía **DPAPI**: llave raw de 32 bytes en `nomina.db.key` (blob CryptProtectData ligado al usuario de Windows) — nunca embebida en el `.exe` ni versionada (`*.db.key` en .gitignore)
- [x] Backups cifrados: exports completos y por-empresa quedan cifrados; `password` opcional los re-cifra con `PRAGMA rekey` para portabilidad a otro equipo; imports detectan cifrado, prueban llave interna y piden contraseña en GUI si hace falta
- [x] Protección anti-cuarentena: si la BD está cifrada y no abre (falta `.key`), la app avisa y sale en vez de tratarla como corrupta
- ⚠️ `reports/`, `fotos/`, `logos/` y exports Excel **siguen en claro en disco** — riesgo residual documentado
- [x] Exactitud decimal: toda la aritmética monetaria usa `Decimal` (`D()` + `pesos()` cuantizan a centavos con ROUND_HALF_UP) en `calcular_nomina`, `calcular_pago_prestacion`, préstamos, contratos, liquidaciones y todos los INSERT/UPDATE de montos — en vez de migrar columnas a INTEGER, los valores REAL almacenados ya vienen exactos a 2 decimales

## Fase 3 — Autorización ✅ (completada)

- [x] Sesión de seguridad en capa de datos: `establecer_sesion`/`cerrar_sesion`/`sesion_actual` en `database.py`, alimentada desde `app.py` en login/logout/expiración por inactividad
- [x] `_requerir_rol("superadmin")` en CRUD de empresas, gestión de admins y backups completos
- [x] `_verificar_scope_empresa` en ~35 funciones que reciben `empresa_id` (empleados, nóminas, préstamos, contratos, colillas, Excel, bitácora, backup por empresa) — admin solo toca su empresa; superadmin todas
- [x] `_verificar_scope_registro` resuelve `empresa_id` desde la fila cuando la función solo recibe un ID (abonar/eliminar préstamo, colillas, `nomina_existe_id`)
- [x] `verificar_password_usuario`/`cambiar_password_propio` exigen cuenta propia o superadmin (`_verificar_cuenta_propia`)
- [x] Sin sesión activa → `PermissionError` en cualquier función protegida (no hay "modo abierto")
- [x] Pruebas: admin no puede crear/listar en otra empresa, no crea empresas/admins, no resetea passwords, no exporta backup completo, no ve bitácora global; sí opera su empresa; sin sesión se rechaza todo (`_test_all.py`, 10 checks nuevos)

## Fase 4 — Cumplimiento de nómina (pre-requisito DIAN) ✅ (parcial — base de datos/cálculos)

- [x] Tabla `parametros_legales` por año (SMMLV, aux. transporte, UVT, % salud/pensión/FSP, umbral FSP) — sembrada con valores oficiales 2025 (SMMLV 1.423.500 / aux 200.000 / UVT 49.799) y 2026 (1.750.905 / 249.095 / 52.374); CRUD solo superadmin en GUI ("⚖️ Parámetros Legales")
- [x] `parametros_vigentes(periodo)` selecciona los valores del año del período de nómina — los cálculos históricos se reproducen con los parámetros de su año (no el valor vigente de hoy)
- [x] **Auditoría corregida**: salud/pensión ya NO son fijos $70.036 → ahora son % parametrizado del **IBC** en cada pago (quincenal y mensual suman lo mismo); IBC = proporcional + extras + bonificaciones; auxilio de transporte **no** es factor salarial (excluido del IBC) y solo aplica ≤2 SMMLV prorrateado por días; **FSP** 1% del IBC desde 4 SMMLV (parametrizable)
- [x] Columnas nuevas en `nominas`: `fsp`, `ibc`; `auxilio_transporte` (ya existía) ahora se persiste
- [x] Tabla `novedades` (incapacidad, licencias maternidad/paternidad/no remunerada, vacaciones, suspensión, ingreso/retiro, variación salarial, otra) con `TIPOS_NOVEDAD` alineados a conceptos DIAN + CRUD con scope de empresa + sección "📅 Novedades" en GUI
- [x] Colilla PDF actualizada: auxilio de transporte en devengados, FSP en deducciones, salud/pensión etiquetados "% del IBC"
- [ ] Retención en la fuente y prestaciones sociales (prima, cesantías, intereses) — pendiente
- [ ] Novedades aún no descuentan días automáticamente del cálculo de nómina (son registro informativo — el cálculo del efecto en pago queda para la integración DIAN)

## Fase 5 — DIAN (nómina electrónica)

- [ ] Decidir: proveedor tecnológico vs integración directa (directa = XML UBL + firma digital + habilitación propia — mucho más trabajo)
- [ ] Expandir `empleados`: nombres/apellidos separados, tipo documento (CC/CE/PPT), municipio (Divipola), EPS, ARL, fondo pensión, banco, tipo trabajador
- [ ] Expandir `nominas`: `cune`, `estado_transmision`, `fecha_envio`, `fecha_respuesta`, `mensaje_error`, `xml_path`, `ambiente`
- [ ] Generación de XML según Anexo Técnico + consecutivo autorizado
- [ ] Notas de Ajuste (documento de corrección)
- [ ] Check de conectividad + `requests` al proveedor/DIAN; credenciales en config externo, no en código
- [ ] Toggle habilitación/producción + set de pruebas DIAN antes de producción
- [ ] Alerta de plazo legal (transmitir dentro del plazo reglamentario del mes siguiente)
- [ ] QR + CUNE en la colilla PDF (validación tipo factura electrónica)

## Fase 6 — Auditoría y Habeas Data

- [ ] Bitácora: cubrir logins, exports PDF/Excel, consultas de datos sensibles
- [ ] Cadena de hash en bitácora (cada fila firma la anterior — tamper-evidence; documentar que no es inmutable al 100% en local)
- [ ] Aviso de privacidad (Ley 1581 de 2012) en app + PDF de consentimiento firmable; campo `consentimiento_fecha`/`doc_path` en empleados
- [ ] Respetar retención legal (mínimo 10 años, C. de Comercio art. 60) antes de borrado definitivo
- [ ] Firma de código del `.exe` + `upx=False` en `app.spec` (menos falsos positivos de antivirus y bloqueos de SmartScreen)

---

## Notas técnicas

- Solo existen roles `superadmin` y `admin`; el riesgo principal es escala horizontal (admin → otra empresa), no un tercer rol.
- `paths.py` ya crea `reports/`; `logos/` y `fotos/` se crean lazy en `crear_carpetas_imagenes()`.
- El suite `_test_all.py` + `_test_gui.py` ya es el gate pre-build (ver AGENTS.md).
