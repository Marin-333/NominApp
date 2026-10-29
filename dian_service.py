"""
dian_service.py — Puente de transmisión DIAN (Nómina Electrónica).

MODO SIMULADO (local): la app se usa de manera interna y todavía no hay
proveedor tecnológico ni certificado de firma digital contratado. Todas las
funciones devuelven respuestas simuladas que mantienen el flujo completo
(generado → transmitido → aceptado/rechazado) para pruebas locales.

CUANDO HAYA PROVEEDOR: reemplazar `transmitir_documento()` por la llamada
real a la API del proveedor (o al SOAP `SendNominaSync` de la DIAN si se va
directo). Los puntos de integración reales están marcados con `INTEGRAR:`.
Los estados, el CUNE y el XML ya se generan de verdad en `database.py` —
aquí solo se simula el envío y la respuesta.
"""

import hashlib
from datetime import datetime

# Ambiente simulado activo. Cambiar a False cuando haya credenciales reales.
MODO_SIMULADO = True

# URL base del proveedor / DIAN (rellenar cuando se contrate).
PROVEEDOR_URL = None          # INTEGRAR: endpoint real, p.ej. https://api.proveedor.co
PROVEEDOR_API_KEY = None      # INTEGRAR: credenciales en config externo, nunca en código


def proveedor_configurado():
    """True cuando existan credenciales reales de transmisión."""
    return not MODO_SIMULADO and bool(PROVEEDOR_URL and PROVEEDOR_API_KEY)


def transmitir_documento(doc, xml_contenido):
    """Envía el XML del documento al canal de transmisión.

    `doc`: fila de documentos_dian (dict) con numero, cune, ambiente…
    `xml_contenido`: el XML generado (str).

    Devuelve (ok, estado, respuesta):
      - ok:        bool — la DIAN/proveedor aceptó el envío
      - estado:    'transmitido'|'rechazado'
      - respuesta: str  — mensaje / ApplicationResponse resumido
    """
    if not proveedor_configurado():
        return _respuesta_simulada(doc)
    # INTEGRAR: POST real al proveedor/DIAN aquí:
    #   resp = requests.post(PROVEEDOR_URL + "/nominas", xml_contenido,
    #                        headers={"Authorization": PROVEEDOR_API_KEY})
    return _respuesta_simulada(doc)


def consultar_estado(doc):
    """Consulta el estado de un documento ya transmitido (GetStatus).
    En modo simulado los documentos quedan aceptados al instante."""
    return "aceptado", f"SIMULADO: documento {doc.get('numero','?')} aceptado."


def _respuesta_simulada(doc):
    """ApplicationResponse simulado — imita el flujo real sin salir de la app."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tracking = hashlib.sha256(
        f"{doc.get('cune','')}{ahora}".encode("utf-8")).hexdigest()[:16]
    return True, "transmitido", (
        f"[SIMULADO LOCAL — sin proveedor real] "
        f"Documento {doc.get('numero','?')} recibido en ambiente "
        f"{doc.get('ambiente','habilitacion')}. "
        f"TrackingID: {tracking}. Fecha: {ahora}. "
        f"Al contratar un proveedor esta respuesta será el "
        f"ApplicationResponse real de la DIAN.")
