# Guía de transferencia a otro PC

Cómo mover NominaApp **con todos sus datos** a otro equipo sin perder nada
ni quedar bloqueado por el cifrado.

## Lo importante primero

`nomina.db` está **cifrada** (SQLCipher). La llave está en `nomina.db.key`,
pero ese archivo está protegido con DPAPI de Windows: solo lo puede leer
**tu usuario de Windows en este PC**.

> ⚠️ Copiar `nomina.db` + `nomina.db.key` a otro PC **NO funciona**.
> La llave no se puede descifrar bajo otro usuario/equipo.

La forma correcta de mover los datos es un **backup exportado con
contraseña**.

## Paso a paso

### En el PC actual (origen)

1. Abre NominaApp e inicia sesión.
2. Ve a la sección **Backup**:
   - Superadmin → *Backup General* → **Exportar Backup Completo**
     (lleva todas las empresas), o
   - Admin → *Backup* → **Exportar Backup de Empresa**
     (solo tu empresa).
3. Cuando la app pida contraseña, **escribe una** y guárdala bien.
   Esa contraseña es la que abrirá el backup en el otro equipo.
4. Copia a un USB/carpeta compartida:
   - `dist\app.exe` (el ejecutable)
   - el archivo `.db` del backup que acabas de exportar
   - opcional: las carpetas `logos\` y `fotos\` si quieres conservar
     logos de empresas y fotos de empleados
   - opcional: `reports\` si quieres conservar los PDFs ya generados

### En el PC nuevo (destino)

1. Crea una carpeta para la app (ej. `C:\NominaApp`) y copia `app.exe`
   ahí junto con las carpetas `logos\`, `fotos\`, `reports\` si las traes.
2. Abre `app.exe`. En el primer arranque la app:
   - crea `nomina.db` nueva **ya cifrada**,
   - crea su propio `nomina.db.key` local (no necesitas copiar ninguno),
   - crea las carpetas `reports\`, `backups\`, `logs\` solas.
3. Inicia sesión como `superadmin` (te pedirá definir contraseña nueva
   si es una instalación limpia).
4. Ve a **Backup** → **Restaurar** y elige el `.db` que trajiste.
   La app detecta que está cifrado y te pide la contraseña del paso 3.
5. Reinicia la app cuando lo indique. Listo: tus datos están en el PC
   nuevo, cifrados con la llave de ese equipo.

## Qué NO copiar

| Archivo/carpeta | Por qué |
|---|---|
| `nomina.db` directamente | Cifrada; sin la llave del otro PC no abre |
| `nomina.db.key` | DPAPI solo lo descifra el usuario/equipo original |
| `backups\` | Son copias cifradas con la llave del PC origen |
| `logs\` | Solo diagnóstico local |
| `build\`, `dist\` | Se regeneran al compilar |

## Resumen visual

```
PC origen                          PC nuevo
─────────                          ────────
app.exe ─────────────────────────> app.exe
Backup CON contraseña ───────────> Restaurar (pide contraseña)
                                   → genera su propia nomina.db + .key
logos\ fotos\ reports\ (opcional)> copiar tal cual
nomina.db + .key  ✗ NO copiar
```

## Si algo sale mal

- **"No se pudo descifrar el backup"** → la contraseña no es la del
  export. Vuelve a intentarlo o genera un backup nuevo en el origen.
- **"Falta el archivo de llave"** → copiaste una `nomina.db` cifrada sin
  su `.key`. Bórrala y restaura desde un backup con contraseña.
- **La app abre pero pide login y no entra** → tras restaurar hay que
  reiniciar la app; los usuarios/claves son los del backup restaurado.
