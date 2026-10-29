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

## Fase 2 — Datos en reposo (decisión arquitectónica primero)

- [ ] Decidir: **SQLCipher** (BD completa) vs cifrado por columnas (rompe `UNIQUE(empresa_id, documento)` y búsquedas; requeriría cifrado determinista o blind index)
- [ ] Gestión de llave vía **DPAPI de Windows** (CryptProtectData / pywin32) — nunca embebida en el `.exe` (PyInstaller es reversible)
- [ ] Backups cifrados o con contraseña; documentar que `reports/`, `fotos/` y exports quedan en claro
- [ ] Migrar dinero `REAL` → `INTEGER` (centavos) para evitar errores de redondeo — opcional pero recomendado

## Fase 3 — Autorización ✅ (completada)

- [x] Sesión de seguridad en capa de datos: `establecer_sesion`/`cerrar_sesion`/`sesion_actual` en `database.py`, alimentada desde `app.py` en login/logout/expiración por inactividad
- [x] `_requerir_rol("superadmin")` en CRUD de empresas, gestión de admins y backups completos
- [x] `_verificar_scope_empresa` en ~35 funciones que reciben `empresa_id` (empleados, nóminas, préstamos, contratos, colillas, Excel, bitácora, backup por empresa) — admin solo toca su empresa; superadmin todas
- [x] `_verificar_scope_registro` resuelve `empresa_id` desde la fila cuando la función solo recibe un ID (abonar/eliminar préstamo, colillas, `nomina_existe_id`)
- [x] `verificar_password_usuario`/`cambiar_password_propio` exigen cuenta propia o superadmin (`_verificar_cuenta_propia`)
- [x] Sin sesión activa → `PermissionError` en cualquier función protegida (no hay "modo abierto")
- [x] Pruebas: admin no puede crear/listar en otra empresa, no crea empresas/admins, no resetea passwords, no exporta backup completo, no ve bitácora global; sí opera su empresa; sin sesión se rechaza todo (`_test_all.py`, 10 checks nuevos)

## Fase 4 — Cumplimiento de nómina (pre-requisito DIAN)

- [ ] Tabla `parametros_legales` (SMMLV, aux. transporte, porcentajes, UVT) con vigencias anuales — no hardcodear
- [ ] Auditar cálculos: salud 4% / pensión 4%, FSP >4 SMMLV, retención en la fuente, prestaciones sociales
- [ ] Novedades que la DIAN exige reportar: incapacidades, licencias, vacaciones

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
