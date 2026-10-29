# 💼 NóminaApp — Sistema de Gestión de Nómina Empresarial

Aplicación de escritorio desarrollada en Python con interfaz gráfica (Tkinter),
base de datos local SQLite y generación de PDFs con ReportLab.

---

## 📋 Características

| Módulo | Funcionalidades |
|--------|----------------|
| **Autenticación** | Login seguro, hash SHA-256, roles |
| **Superadministrador** | CRUD empresas, CRUD administradores |
| **Administrador** | CRUD empleados, nómina, historial, reportes |
| **Nómina** | Cálculo automático de deducciones de ley colombiana |
| **PDF** | Colillas de pago individuales, reportes generales |
| **Base de datos** | SQLite local, sin internet |

---

## 🚀 Instalación y ejecución

### Opción A: Ejecutar desde código fuente (desarrollo)

**Requisitos:**
- Python 3.10 o superior
- Windows / Linux / macOS

```bash
# 1. Clonar o descargar el proyecto
cd nomina_app/

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar la aplicación
python app.py
```

### Opción B: Compilar ejecutable .exe (distribución)

```bash
# En Windows, ejecutar:
build.bat

# O manualmente:
pip install pyinstaller
pyinstaller nomina_app.spec --clean

# El ejecutable estará en: dist/NominaApp.exe
```

---

## 🔑 Credenciales por defecto

| Rol | Usuario | Contraseña |
|-----|---------|------------|
| Superadministrador | `superadmin` | `Admin2024!` |

> ⚠️ **Cambia la contraseña del superadmin** modificando la función `init_db()` en `database.py` antes de compilar.

---

## 🏗️ Estructura del proyecto

```
nomina_app/
│
├── app.py              # Aplicación principal (GUI Tkinter)
├── database.py         # Módulo SQLite (modelos y consultas)
├── pdf_generator.py    # Generación de PDFs (ReportLab)
│
├── nomina.db           # Base de datos (generada automáticamente)
├── reports/            # PDFs generados (colillas y reportes)
│
├── requirements.txt    # Dependencias Python
├── nomina_app.spec     # Configuración PyInstaller
├── build.bat           # Script de compilación Windows
└── README.md           # Este archivo
```

---

## 🧮 Cálculo de nómina (Ley colombiana)

```
Salario proporcional = (Salario base / 30) × Días trabajados
Valor horas extras   = Horas × Valor por hora
Salario bruto        = Salario proporcional + Horas extras + Bonificaciones

Salud (empleado)     = Salario bruto × 4%
Pensión (empleado)   = Salario bruto × 4%
Otras deducciones    = Monto ingresado manualmente

Salario neto         = Salario bruto − Salud − Pensión − Otras deducciones
```

---

## 📄 Reportes PDF generados

1. **Colilla de pago individual**: Incluye datos del empleado, devengados,
   deducciones desglosadas, neto a pagar y espacios para firmas.

2. **Reporte general de nómina**: Tabla consolidada de todos los empleados
   por período, con totales y resumen.

Los PDFs se guardan automáticamente en la carpeta `reports/`.

---

## 🔒 Seguridad

- Las contraseñas se almacenan con hash SHA-256 (nunca en texto plano).
- Los administradores solo pueden ver datos de su empresa (`empresa_id`).
- El superadministrador no puede acceder a datos operativos de empleados.
- La base de datos es local y no requiere conexión a internet.

---

## 🛠️ Tecnologías utilizadas

| Librería | Uso |
|----------|-----|
| `tkinter` | Interfaz gráfica (incluida en Python) |
| `sqlite3` | Base de datos local (incluida en Python) |
| `hashlib` | Hash de contraseñas (incluida en Python) |
| `reportlab` | Generación de PDFs |
| `Pillow` | Soporte de imágenes |
| `PyInstaller` | Compilación a .exe |

---

## 📦 Distribución del ejecutable

Al compilar con PyInstaller, obtendrás un único archivo `NominaApp.exe`
que incluye todas las dependencias. Para distribuir la aplicación:

1. Copia `NominaApp.exe` al equipo destino
2. La base de datos `nomina.db` y la carpeta `reports/` se crean automáticamente
   en la misma carpeta donde se ejecute el .exe
3. **No se requiere instalar Python** ni ninguna dependencia adicional

---

## 📞 Soporte y extensiones sugeridas

- Agregar módulo de **liquidaciones y prestaciones** (cesantías, prima, vacaciones)
- Importar/exportar empleados desde **Excel**
- Envío de colillas por **correo electrónico**
- Módulo de **auditoría y logs**
- Soporte para múltiples tipos de contrato

---

*Desarrollado con Python + Tkinter + SQLite + ReportLab*
