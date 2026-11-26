from flask import Flask, jsonify
from flask_cors import CORS
from psycopg.rows import dict_row
from pathlib import Path
from dotenv import load_dotenv
import psycopg
import os
import hashlib
import json

app = Flask(__name__)

CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:8080",
            "http://127.0.0.1:8080"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# =====================================================
# 1. CONEXION A NEON
# =====================================================

BASE = Path(__file__).resolve().parent.parent.parent

load_dotenv(
    BASE / "Base_Datos" / ".env",
    override=True
)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No se encontro DATABASE_URL")

# =====================================================
# 2. MAPA DE MESES
# =====================================================

MESES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre"
}

# =====================================================
# 3. MAPA DE RESPONSABLES
# =====================================================

RESPONSABLES = {
    "E_MEC 00": "Mecanico",
    "E_SOP 03": "Soplado",
    "E_MEC 01": "Soplado",
    "E_ELE 05": "Electrico",
    "E_MEC 03": "Mecanico",
    "E_MEC 09": "Mecanico",
    "E_ELE 00": "Electrico",
    "E_ELE 02": "Electrico",
    "E_MEC 02": "Mecanico",
    "E_ELE 03": "Electrico",
    "E_MEC 06": "Mecanico",
    "E_MEC 07": "Mecanico",
    "E_IEF 03": "Infraestructura",
    "E_IEF 01": "Infraestructura",
    "E_ELE 04": "Electrico",
    "E_SOP 01": "Soplado",
    "E_AUT 00": "Automatizacion",
    "E_ELE 01": "Electrico",
    "E_OPE 00": "Operativo",
    "E_INS 01": "Instrumentacion",
    "E_IEF 00": "Infraestructura",
    "E_MEC 08": "Mecanico",
    "E_MEC 04": "Mecanico",
    "E_AUT 01": "Automatizacion",
    "E_MEC 05": "Mecanico",
    "E_ACM 01": "Calidad"
}

# =====================================================
# 4. CLASIFICACION DE AVISOS
# =====================================================

CLASIFICACIONES = {
    "ZS": "Emergente",
    "ZA": "Correctivo"
}

# =====================================================
# 5. ESTADOS SAP IW28
# =====================================================

ESTADOS = {
    "MEAB": "Aviso abierto",
    "MECE": "Aviso cerrado",
    "MECE ORAS": "Aviso cerrado",
    "METR ORAS": "Aviso cerrado",
    "METR MIMP ORAS": "Aviso cerrado"
}

from datetime import datetime, date, time


def convertir_fecha(valor):

    if valor is None or valor == "":
        return None

    if isinstance(valor, datetime):
        return valor.date()

    if isinstance(valor, date):
        return valor

    texto = str(valor).strip()

    formatos = [
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S"
    ]

    for formato in formatos:
        try:
            return datetime.strptime(
                texto, formato
            ).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        app.logger.warning("Fecha no reconocida: %s", texto)
        return None


def convertir_hora(valor):

    if valor is None or valor == "":
        return ""

    if isinstance(valor, (datetime, time)):
        return valor.strftime("%H:%M:%S")

    texto = str(valor).strip()

    for formato in ["%H:%M:%S", "%H:%M", "%H%M%S"]:

        try:
            return datetime.strptime(
                texto, formato
            ).strftime("%H:%M:%S")

        except ValueError:
            continue

    try:
        return time.fromisoformat(texto).strftime("%H:%M:%S")
    except ValueError:
        return texto


def formatear_actualizacion(valor):

    if valor is None:
        return ""

    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M:%S")

    texto = str(valor).strip()

    try:
        return datetime.fromisoformat(
            texto
        ).strftime("%d/%m/%Y %H:%M:%S")

    except ValueError:
        return texto
# =====================================================
# 6. VERIFICAR ESTADO DE LA API
# =====================================================

@app.route("/api/estado", methods=["GET"])
def estado():

    return jsonify({
        "ok": True,
        "servicio": "API Consulta Avisos SAP",
        "estado": "Disponible"
    })

# =====================================================
# 7. CONSULTAR AVISOS DESDE NEON
# =====================================================

@app.route("/api/iw28/consultar", methods=["GET"])
def consultar_iw28():

    try:

        # CONSULTAR BASE DE DATOS
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=20,
            row_factory=dict_row
        ) as conexion:

            with conexion.cursor() as cursor:

                cursor.execute("""
                    SELECT *
                    FROM public.maintince
                    ORDER BY fecha_aviso ASC NULLS LAST,
                             notificacion ASC
                """)

                registros = cursor.fetchall()

        datos = []

        # OBTENER ULTIMA ACTUALIZACION
        ultima_actualizacion = max(
            (
                fila["fecha_actualizacion"]
                for fila in registros
                if fila["fecha_actualizacion"] is not None
            ),
            default=None
        )

        # PROCESAR INFORMACION
        for fila in registros:

            fecha = convertir_fecha(fila["fecha_aviso"])
            hora = convertir_hora(fila["hora_creacion"])

            puesto = (
                fila["puesto_trabajo"] or ""
            ).strip()

            status = " ".join(
                (fila["status_sistema"] or "").split()
            )

            orden = (
                str(fila["orden"]).strip()
                if fila["orden"] is not None
                else ""
            ) or None

            responsable = RESPONSABLES.get(
                puesto,
                "Sin clasificar"
            )

            clasificacion = CLASIFICACIONES.get(
                (fila["clase_aviso"] or "").strip(),
                "Mal levantada"
            )

            estado_aviso = ESTADOS.get(
                status,
                "REVISAR"
            )

            # ESTADO DE TIEMPO PROVISIONAL
            # Pendiente incorporar IW47
            if status == "MECE" and not orden:

                estado_tiempo = "DENTRO DE TIEMPO"

            elif orden:

                estado_tiempo = "POR NOTIFICAR"

            else:

                estado_tiempo = "FUERA DE TIEMPO"

            # ESTADO FINAL PROVISIONAL
            # Pendiente incorporar IW39
            if estado_aviso == "Aviso cerrado" and orden:

                estado_final = "REVISAR"

            else:

                estado_final = estado_aviso

            # FORMATO JSON PARA LA PAGINA WEB
            datos.append({

                "mes": MESES.get(fecha.month, "")
                    if fecha else "",

                "clasificacion": clasificacion,

                "responsable": responsable,

                "notificacion": str(
                    fila["notificacion"]
                ),

                "orden": orden,

                "descripcion":
                    fila["descripcion"] or "",

                "descripcion1":
                    fila["descripcion_1"] or "",

                "descripcion2":
                    fila["descripcion_2"] or "",

                "fechaAviso":
                    fecha.strftime("%d/%m/%Y")
                    if fecha else "",

                "horaAviso": hora,

                "ptoTrabajo": puesto,

                "fechaContab": "",

                "dias": None,

                "estadoTiempo": estado_tiempo,

                "estadoAviso": estado_aviso,

                "statusAvisoIW28": status,

                "estadoOT": None,

                "estadoSAPOrden": None,

                "textoNotificacion": "",

                "costoReal": None,

                "estadoFinal": estado_final

            })

        # GENERAR VERSION ESTABLE
        version = hashlib.sha256(
            json.dumps(
                datos,
                ensure_ascii=False,
                sort_keys=True
            ).encode("utf-8")
        ).hexdigest()[:16]

        # RESPUESTA FINAL
        respuesta = {
            "ok": True,

            "lastUpdate":
                ultima_actualizacion.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
                if ultima_actualizacion else "",

            "version": version,

            "fromCache": False,

            "cantidadAvisos": len(datos),

            "data": datos
        }

        print(f"Consulta correcta: {len(datos)} avisos")

        return jsonify(respuesta)

    except Exception as error:

      app.logger.exception("Error consultando Neon")

      return jsonify({ 
        "ok": False,
        "mensaje": "Error consultando Neon",
        "error": type(error).__name__
        }), 500


# =====================================================
# 8. INICIAR API
# =====================================================

if __name__ == "__main__":

    app.json.sort_keys = False
    app.json.ensure_ascii = False

    print("====================================")
    print("MANTENIMIENTO V5")
    print("API CONSULTA AVISOS SAP")
    print("BASE DE DATOS: NEON POSTGRESQL")
    print("PUERTO: 5002")
    print("====================================")

    app.run(
        host="127.0.0.1",
        port=5002,
        debug=False,
        threaded=True,
        use_reloader=False
    )
