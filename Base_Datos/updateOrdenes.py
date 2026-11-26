from flask import Flask, jsonify
import pandas as pd
import psycopg
from psycopg import sql
import os
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

app = Flask(__name__)
BASE = Path(__file__).resolve().parent
RUTA_ENV = BASE.parent / '.env'
load_dotenv(RUTA_ENV, override=True)
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError(f'No se encontro DATABASE_URL en {RUTA_ENV}')

archivo = BASE.parent / 'Descarga_Informacion' / 'Ordenes' / 'IW39_ORDENES.xlsx'
bloqueo = threading.Lock()
TABLA = sql.Identifier('public', 'ordenes')

# Encabezado original, nombre SQL, tipo PostgreSQL.
# Las dos columnas Descripción se distinguen por su posicion.
CAMPOS = [
    ('Orden', 'orden', 'TEXT'),
    ('Ubicac.técnica', 'ubicacion_tecnica', 'TEXT'),
    ('Descripción', 'descripcion_ubicacion', 'TEXT'),
    ('Equipo', 'equipo', 'TEXT'),
    ('Descripción', 'descripcion_equipo', 'TEXT'),
    ('Texto breve', 'texto_breve', 'TEXT'),
    ('Pto.tbjo.resp.', 'puesto_trabajo_responsable', 'TEXT'),
    ('Status sistema', 'status_sistema', 'TEXT'),
    ('Liberación real', 'liberacion_real', 'DATE'),
    ('Fe.inic.extrema', 'fecha_inicio_extrema', 'DATE'),
    ('Fecha ref.', 'fecha_referencia', 'DATE'),
    ('FechaTerminac.', 'fecha_terminacion', 'DATE'),
    ('Fecha entrada', 'fecha_entrada', 'DATE'),
    ('Fe.fin extrema', 'fecha_fin_extrema', 'DATE'),
    ('Fe.inicio real', 'fecha_inicio_real', 'DATE'),
    ('Fecha llamada', 'fecha_llamada', 'DATE'),
    ('Fecha modific.', 'fecha_modificacion', 'DATE'),
    ('Fe.planificada', 'fecha_planificada', 'DATE'),
    ('Fecha fin real', 'fecha_fin_real', 'DATE'),
    ('Fecha ref.clie.', 'fecha_referencia_cliente', 'DATE'),
    ('Gpo.hojas ruta', 'grupo_hojas_ruta', 'TEXT'),
    ('Plan mant.prev.', 'plan_mantenimiento', 'TEXT'),
    ('TotalCostesPlan', 'coste_total_plan', 'NUMERIC'),
    ('Centro coste', 'centro_coste', 'TEXT'),
    ('Clase de orden', 'clase_orden', 'TEXT'),
    ('Cl.actividad PM', 'clase_actividad_pm', 'TEXT'),
    ('Notificación', 'notificacion', 'TEXT'),
    ('Prioridad', 'prioridad', 'TEXT'),
    ('CeCo responsab.', 'centro_coste_responsable', 'TEXT'),
    ('Cst.tot.reales', 'coste_total_real', 'NUMERIC'),
]
COLUMNAS = [nombre for _, nombre, _ in CAMPOS]
ESPERADAS = [original for original, _, _ in CAMPOS]
# Para importes guardados como texto (no celdas numericas de Excel).
# Cambiar a '.' si SAP exporta texto con punto decimal.
DECIMAL_TEXTO = ','


def normalizar(valor):
    return ' '.join(str(valor).strip().split()).casefold()


def leer_ordenes():
    # Examina hasta 20 filas de cada hoja y exige los 30 encabezados
    # consecutivos en el orden proporcionado. Prefiere la segunda hoja.
    if not archivo.is_file():
        raise FileNotFoundError(f'No se encontro el Excel: {archivo}')
    esperadas = [normalizar(c) for c in ESPERADAS]
    with pd.ExcelFile(archivo, engine='openpyxl') as libro:
        hojas = libro.sheet_names
        candidatas = hojas[1:2] + hojas[:1] + hojas[2:]
        for hoja in candidatas:
            vista = pd.read_excel(libro, sheet_name=hoja, header=None,
                                 nrows=20, dtype=object, keep_default_na=False)
            for fila, valores in enumerate(vista.itertuples(index=False, name=None)):
                valores = [normalizar(v) for v in valores]
                for inicio in range(len(valores) - len(esperadas) + 1):
                    if valores[inicio:inicio+len(esperadas)] == esperadas:
                        datos = pd.read_excel(
                            libro, sheet_name=hoja, header=None,
                            skiprows=fila+1,
                            usecols=list(range(inicio, inicio+len(esperadas))),
                            dtype=object, keep_default_na=False)
                        datos.columns = COLUMNAS
                        return datos, hoja
    raise ValueError('No se encontraron los 30 encabezados IW39 en el orden esperado')


def texto(valor):
    if pd.isna(valor):
        return None
    valor = str(valor).strip()
    return valor or None


def convertir_fecha(valor):
    if pd.isna(valor) or str(valor).strip() in ('', '00.00.0000', '00000000'):
        return None
    if isinstance(valor, (datetime, date)):
        return valor.date() if isinstance(valor, datetime) else valor
    # Los numeros de serie Excel se interpretan solo si son plausibles.
    if isinstance(valor, (int, float)) and 20000 <= valor <= 80000:
        return pd.to_datetime(valor, unit='D', origin='1899-12-30').date()
    return pd.to_datetime(str(valor).strip(), dayfirst=True,
                          format='mixed', errors='raise').date()


def convertir_coste(valor):
    if pd.isna(valor) or str(valor).strip() == '':
        return None
    if isinstance(valor, (int, float, Decimal)):
        resultado = Decimal(str(valor))
    else:
        cadena = str(valor).strip().replace(' ', '').replace('\xa0', '')
        if cadena.endswith('-'):
            cadena = '-' + cadena[:-1]
        if DECIMAL_TEXTO == ',':
            cadena = cadena.replace('.', '').replace(',', '.')
        else:
            cadena = cadena.replace(',', '')
        resultado = Decimal(cadena)
    if not resultado.is_finite():
        raise ValueError('Importe no finito')
    return resultado


def actualizar_neon():
    inicio = time.perf_counter()
    print('Leyendo Excel IW39...', flush=True)
    datos, hoja = leer_ordenes()
    datos['orden'] = datos['orden'].map(texto)
    # Excluye filas vacias, titulos y totales. No elimina ceros iniciales.
    validas = datos['orden'].map(lambda v: bool(v and v.isascii() and v.isdigit()))
    descartados = int((~validas).sum())
    datos = datos.loc[validas].copy()
    if datos.empty:
        raise ValueError('No hay ordenes validas; se conserva la tabla anterior')
    for _, nombre, tipo in CAMPOS:
        funcion = {'DATE': convertir_fecha, 'NUMERIC': convertir_coste}.get(tipo, texto)
        try:
            datos[nombre] = datos[nombre].map(funcion)
        except (ValueError, TypeError, InvalidOperation, OverflowError) as error:
            raise ValueError(f'Valor invalido en columna {nombre}: {error}') from error
    datos = datos.astype(object).where(pd.notna(datos), None)
    # IW39 es una lista de cabeceras de orden. Si hay duplicados distintos,
    # se cancela en lugar de perder informacion arbitrariamente.
    antes = len(datos)
    datos = datos.drop_duplicates()
    duplicados_identicos = antes - len(datos)
    if datos['orden'].duplicated().any():
        raise ValueError('Hay ordenes repetidas con datos distintos; revisar Excel antes de cargar')
    columnas_sql = sql.SQL(', ').join(map(sql.Identifier, COLUMNAS))
    definiciones = sql.SQL(', ').join(
        sql.SQL('{} {}').format(sql.Identifier(n), sql.SQL(t)) for _, n, t in CAMPOS)
    print(f'Cargando {len(datos)} ordenes desde {hoja} a Neon...', flush=True)
    # Reemplazo atomico del historico completo. Si falla, se revierte todo.
    with psycopg.connect(DATABASE_URL, connect_timeout=20) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} ({}, fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT NOW())').format(TABLA, definiciones))
            cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(TABLA))
            with cursor.copy(sql.SQL('COPY {} ({}) FROM STDIN').format(TABLA, columnas_sql)) as copia:
                for fila in datos.itertuples(index=False, name=None):
                    copia.write_row(fila)
            cursor.execute(sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS iw39_orden_uidx ON {} (orden)').format(TABLA))
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS iw39_notificacion_idx ON {} (notificacion)').format(TABLA))
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS iw39_fecha_entrada_idx ON {} (fecha_entrada)').format(TABLA))
    resultado = {'ok': True, 'mensaje': 'Sincronizacion IW39 completada',
                 'hoja': hoja, 'procesados': len(datos),
                 'filas_descartadas': descartados,
                 'duplicados_identicos': duplicados_identicos,
                 'segundos': round(time.perf_counter()-inicio, 2)}
    print(resultado, flush=True)
    return resultado


@app.get('/api/estado')
def estado():
    return jsonify(ok=True, servicio='API SAP IW39 - Neon')


@app.post('/api/iw39/actualizar')
def actualizar_iw39():
    if not bloqueo.acquire(blocking=False):
        return jsonify(ok=False, mensaje='Ya existe una carga en proceso'), 409
    try:
        return jsonify(actualizar_neon()), 200
    except Exception as error:
        print('ERROR:', type(error).__name__, str(error), flush=True)
        return jsonify(ok=False, error=type(error).__name__,
                       mensaje='Error durante la sincronizacion; revise la terminal'), 500
    finally:
        bloqueo.release()


if __name__ == '__main__':
    print('API SAP IW39 - NEON | Puerto 5004', flush=True)
    app.run(host='127.0.0.1', port=5004, debug=False, threaded=True, use_reloader=False)
