
from flask import Flask, jsonify
import pandas as pd
import psycopg
import os
import threading
from pathlib import Path
from dotenv import load_dotenv

app = Flask(__name__)

# 1. CONFIGURACION DE NEON
BASE = Path(__file__).resolve().parent

load_dotenv(BASE.parent / ".env", override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No se encontro DATABASE_URL")

# 2. RUTA DEL EXCEL
archivo = (
    BASE.parent
    / "Descarga_Informacion"
    / "Avisos"
    / "IW28_AVISOS.xlsx"
)

# Evitar dos cargas simultaneas
bloqueo = threading.Lock()

# 3. COLUMNAS
COLUMNAS = [
    "notificacion",
    "orden",
    "fecha_aviso",
    "ubicacion_tecnica",
    "descripcion",
    "descripcion_1",
    "descripcion_2",
    "status_sistema",
    "fecha_referencia",
    "fecha_referencia_cliente",
    "hora_creacion",
    "puesto_trabajo",
    "centro_coste",
    "prioridad",
    "autor_aviso",
    "plan_mantenimiento",
    "clase_aviso"
]

# 4. FUNCION PARA CARGAR EXCEL A NEON
def actualizar_neon():

    if not archivo.exists():
        raise FileNotFoundError(
            f"No se encontro el Excel: {archivo}"
        )

    print("Leyendo Excel IW28...")

    datos = pd.read_excel(archivo, dtype=str)

    datos.columns = COLUMNAS

    # Convertir fechas
    for columna in [
        "fecha_aviso",
        "fecha_referencia",
        "fecha_referencia_cliente"
    ]:
        datos[columna] = pd.to_datetime(
            datos[columna],
            dayfirst=True,
            format="mixed",
            errors="coerce"
        ).dt.date

    # Convertir horas
    datos["hora_creacion"] = pd.to_datetime(
        datos["hora_creacion"],
        format="mixed",
        errors="coerce"
    ).dt.time

    # Limpiar registros
    datos["notificacion"] = (
        datos["notificacion"].str.strip()
    )

    datos = datos.dropna(
        subset=["notificacion"]
    )

    datos = datos[
        datos["notificacion"] != ""
    ]

    datos = datos.drop_duplicates(
        subset=["notificacion"],
        keep="last"
    )

    datos = datos.astype(object).where(
        pd.notna(datos),
        None
    )

    registros = list(
        datos.itertuples(index=False, name=None)
    )

    if not registros:
        raise ValueError("El Excel no contiene avisos")

    # Preparar consulta SQL
    columnas = ", ".join(COLUMNAS)

    valores = ", ".join(
        ["%s"] * len(COLUMNAS)
    )

    actualizar = ", ".join(
        f"{col} = EXCLUDED.{col}"
        for col in COLUMNAS
        if col != "notificacion"
    )

    comparar = " OR ".join(
        f"maintince.{col} IS DISTINCT FROM EXCLUDED.{col}"
        for col in COLUMNAS
        if col != "notificacion"
    )

    consulta = f"""
        INSERT INTO public.maintince ({columnas})
        VALUES ({valores})

        ON CONFLICT (notificacion)

        DO UPDATE SET
            {actualizar},
            fecha_actualizacion = NOW()

        WHERE {comparar}
    """

    # Conectar a Neon
    print("Conectando a Neon...")

    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=20
    ) as conexion:

        with conexion.cursor() as cursor:

            cursor.executemany(
                consulta,
                registros
            )

            modificados = cursor.rowcount

        conexion.commit()

    resultado = {
        "ok": True,
        "mensaje": "Sincronizacion completada",
        "procesados": len(registros),
        "insertados_actualizados": modificados,
        "sin_cambios": len(registros) - modificados
    }

    print(resultado)

    return resultado


# 5. API PARA VERIFICAR FUNCIONAMIENTO
@app.route("/api/estado", methods=["GET"])
def estado():

    return jsonify({
        "ok": True,
        "servicio": "API SAP IW28 - Neon",
        "estado": "Disponible"
    })


# 6. API PARA SOLICITAR ACTUALIZACION
@app.route("/api/iw28/actualizar", methods=["POST"])
def actualizar_iw28():

    if not bloqueo.acquire(blocking=False):

        return jsonify({
            "ok": False,
            "mensaje": "Ya existe una carga en proceso"
        }), 409

    try:

        resultado = actualizar_neon()

        return jsonify(resultado), 200

    except Exception as error:

        print(
            "ERROR:",
            type(error).__name__
        )

        return jsonify({
            "ok": False,
            "error": type(error).__name__,
            "mensaje": "Error durante la sincronizacion"
        }), 500

    finally:

        bloqueo.release()


# 7. INICIAR SERVIDOR API
if __name__ == "__main__":

    print("====================================")
    print("API SAP IW28 - NEON")
    print("Servidor disponible en puerto 5001")
    print("Esperando solicitudes...")
    print("====================================")

    app.run(
        host="127.0.0.1",
        port=5001,
        debug=False,
        threaded=True,
        use_reloader=False
    )
