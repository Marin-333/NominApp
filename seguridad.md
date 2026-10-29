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
- [x] Retención en la fuente (Art. 383 ET): tabla UVT completa con rangos y tarifa marginal, base = gravable − deducciones obligatorias − 25% exento (tope 240 UVT), proyectada a mensual y prorrateada en quincena; columna `retencion` en `nominas`, fila en la colilla PDF y elemento `RetencionFuente` en el XML
- [x] Prestaciones sociales: tabla `prestaciones` + `generar_prestacion` masivo con `calcular_prestacion` (prima/cesantías = (salario+aux)×días/360, intereses = cesantías×12%×días/360, vacaciones = salario×días/720); base con parámetros del año devengado; sección "🏖️ Prestaciones" en GUI (generar, marcar pagada, eliminar) y conceptos `Primas`/`Cesantias`/`Vacaciones` en el XML del mes de pago
- [x] Novedades con efecto en el pago: `licencia_no_remunerada`/`suspension` restan días trabajados; `incapacidad` paga los 2 primeros días del mes al 100% y el resto al 66.67%; los ajustes quedan anotados en `nominas.observaciones` y se emiten en la sección `<Novedades>` del XML

## Fase 5 — DIAN (nómina electrónica) ✅ (fundación — sin transmisión real)

- [x] `empleados` expandido con campos DIAN: nombres/apellidos separados (con auto-split heurístico del nombre), `tipo_documento`, `tipo_trabajador`, `subtipo_trabajador`, `tipo_contrato`, `municipio_laboral` (Divipola), `alto_riesgo_pension`, EPS, ARL, fondo de pensiones, fondo de cesantías, banco, `fecha_retiro` — todos editables en el formulario de empleado (sección "Datos DIAN")
- [x] `empresas` con `departamento`, `municipio` (Divipola) y `pais` — editables en el formulario de empresa
- [x] `documentos_dian` (no `nominas`): 1 documento mensual por trabajador — agrega todas las nóminas del período (quincenas sumadas). Campos: `numero`/`prefijo`, `cune`, `xml_path`, `ambiente`, `estado` (generado→transmitido→aceptado/rechazado), `fecha_envio`, `fecha_respuesta`, `respuesta`, `doc_origen_id` (para notas de ajuste)
- [x] Generación de XML `NominaIndividual` (estructura del Anexo Técnico Res. 000013 — **pendiente validar contra el set de pruebas de habilitación**): Periodo, NumeroSecuenciaXML, LugarGeneracionXML, InformacionGeneralXML (con `SoftwareSecurityCode` SHA-384 y CUNE), EmpleadorXML, TrabajadorXML, PagoXML, FechasPagos, DevengadosXML (Basico/Transporte/HorasExtras/Bonificaciones), DeduccionesXML (Salud/FondoPension/FondoSP/Libranzas/OtrasDeducciones) y totales — `database.py: generar_documento_dian`, `generar_documentos_periodo`
- [x] **CUNE** = SHA-384 de concatenación estricta (NumNE+FecNE+HorNE+ValDev+ValDed+ValTol+NitNE+DocEmp+TipoXML+SoftwarePIN+TipAmb)
- [x] `config_dian` por empresa: prefijo, consecutivo, ambiente (habilitación/producción), Software ID/PIN — nueva sección GUI "🏛️ DIAN" en AdminWin
- [x] Notas de Ajuste: `generar_nota_ajuste` crea documento tipo 'ajuste' referenciando el CUNE/número del original transmitido
- [x] Check de conectividad (`verificar_conectividad`, HEAD request con timeout) + validador `empleados_sin_datos_dian` que lista los campos faltantes por empleado
- [x] Alerta de plazo en la sección DIAN: días restantes hasta el día 10 del mes siguiente (`fecha_limite_dian`/`dias_plazo_dian`)
- [x] Backup/export actualizados: las nuevas tablas viajan en el backup de empresa (con remapeo de ids y de `doc_origen_id`); el borrado definitivo de empleado maneja la FK de `documentos_dian`
- [x] Puente de transmisión `dian_service.py` en **modo simulado local**: `transmitir_documento_dian` recorre el flujo real (generado → transmitido → aceptado/rechazado) con respuestas simuladas; al contratar proveedor solo se cambia la función `transmitir_documento()` — los puntos de integración están marcados con `INTEGRAR:` (decisión documentada con Gemini: sin inversión hasta producción)
- [ ] **Transmisión real**: proveedor tecnológico vs directo sigue como decisión pendiente — falta firma XMLDSig/XAdES-BES, `SendNominaSync`/`GetStatus` o API del proveedor, y credenciales en config externo
- [ ] QR + CUNE en la representación gráfica PDF del documento mensual
- [ ] XML de notas de ajuste con estructura `NominaIndividualDeAjuste` completa (hoy es el documento estándar + nota de referencia)

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
