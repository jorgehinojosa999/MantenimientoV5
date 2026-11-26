from flask import Flask, jsonify
import pandas as pd
import psycopg
from psycopg import sql
import os
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

app = Flask(__name__)
BASE = Path(__file__).resolve().parent
RUTA_ENV = BASE.parent / '.env'
load_dotenv(RUTA_ENV, override=True)
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError(f'No se encontro DATABASE_URL en {RUTA_ENV}')

archivo = BASE.parent / 'Descarga_Informacion' / 'Notificaciones' / 'IW47_NOTIFICACIONES.xlsx'
bloqueo = threading.Lock()

# Encabezado original, nombre SQL y tipo PostgreSQL.
CAMPOS = [
    ('Orden', 'orden', 'TEXT'),
    ('Notificación', 'notificacion', 'TEXT'),
    ('Creado por', 'creado_por', 'TEXT'),
    ('Pto.trab.(plan)', 'puesto_trabajo_plan', 'TEXT'),
    ('Pto.trab.(real)', 'puesto_trabajo_real', 'TEXT'),
    ('Ubicac.técnica', 'ubicacion_tecnica', 'TEXT'),
    ('Estado sistema', 'estado_sistema', 'TEXT'),
    ('Trabajo real', 'trabajo_real', 'TEXT'),
    ('Un.trab.(real)', 'unidad_trabajo_real', 'TEXT'),
    ('Clase orden', 'clase_orden', 'TEXT'),
    ('Equipo', 'equipo', 'TEXT'),
    ('Texto notific.', 'texto_notificacion', 'TEXT'),
    ('Grado realizac.', 'grado_realizacion', 'TEXT'),
    ('Un.trab.(plan)', 'unidad_trabajo_plan', 'TEXT'),
    ('Trabajo (plan)', 'trabajo_plan', 'TEXT'),
    ('Notific.final', 'notificacion_final', 'TEXT'),
    ('Reserv.abiertas', 'reservas_abiertas', 'TEXT'),
    ('Criterio cálc.', 'criterio_calculo', 'TEXT'),
    ('Fecha contab.', 'fecha_contabilizacion', 'DATE'),
    ('Creado el', 'fecha_creacion', 'DATE'),
    ('Mot.desviac.', 'motivo_desviacion', 'TEXT'),
    ('Un.dur.real', 'unidad_duracion_real', 'TEXT'),
    ('Dur.real notif.', 'duracion_real_notificacion', 'TEXT'),
    ('Fecha fin de ejec.', 'fecha_fin_ejecucion', 'DATE'),
    ('Hora fin de ejecuc.', 'hora_fin_ejecucion', 'TIME'),
    ('Grupo planif.', 'grupo_planificacion', 'TEXT'),
    ('Fecha inicio ejec.', 'fecha_inicio_ejecucion', 'DATE'),
    ('Hora inic.ejecución', 'hora_inicio_ejecucion', 'TIME'),
    ('Centro planif.', 'centro_planificacion', 'TEXT'),
    ('Cl.act.(plan)', 'clase_actividad_plan', 'TEXT'),
    ('Cl.activ.(real)', 'clase_actividad_real', 'TEXT'),
    ('Sin activ.rest.', 'sin_actividad_restante', 'TEXT'),
    ('Mandante', 'mandante', 'TEXT'),
    ('Un.dur.restante', 'unidad_duracion_restante', 'TEXT'),
    ('Durac.rest.', 'duracion_restante', 'TEXT'),
    ('Un.trbjo rest.', 'unidad_trabajo_restante', 'TEXT'),
    ('Tbjo.restante', 'trabajo_restante', 'TEXT'),
    ('Fecha final', 'fecha_final', 'DATE'),
    ('Hora de fin', 'hora_fin', 'TIME'),
    ('Nº personal', 'numero_personal', 'TEXT'),
    ('Centro (plan)', 'centro_plan', 'TEXT'),
    ('Notif.estándar', 'notificacion_estandar', 'TEXT'),
    ('Anulado', 'anulado', 'TEXT'),
    ('Confirm.canc.', 'confirmacion_cancelada', 'TEXT'),
    ('Idioma', 'idioma', 'TEXT'),
    ('Suboperación', 'suboperacion', 'TEXT'),
    ('Operación', 'operacion', 'TEXT'),
    ('Centro (real)', 'centro_real', 'TEXT'),
]
COLUMNAS = [nombre for _, nombre, _ in CAMPOS]
TABLA = sql.Identifier('public', 'notificaciones')


def actualizar_neon():
    inicio = time.perf_counter()
    if not archivo.is_file():
        raise FileNotFoundError(f'No se encontro el Excel: {archivo}')
    print('Leyendo segunda hoja IW47...', flush=True)
    datos = pd.read_excel(archivo, sheet_name=1, dtype=str, keep_default_na=False)
    datos.columns = [str(c).strip() for c in datos.columns]
    esperadas = [original for original, _, _ in CAMPOS]
    faltantes = [c for c in esperadas if c not in datos.columns]
    if faltantes:
        raise ValueError(f'Faltan encabezados IW47: {faltantes}')
    datos = datos[esperadas].copy()
    datos.columns = COLUMNAS
    datos = datos.apply(lambda col: col.str.strip())
    datos = datos.loc[(datos['orden'] != '') & (datos['notificacion'] != '')].copy()
    if datos.empty:
        raise ValueError('No hay confirmaciones validas; se conserva la tabla anterior')
    for _, nombre, tipo in CAMPOS:
        if tipo == 'DATE':
            datos[nombre] = pd.to_datetime(datos[nombre], dayfirst=True, format='mixed', errors='coerce').dt.date
        elif tipo == 'TIME':
            datos[nombre] = pd.to_datetime(datos[nombre], format='mixed', errors='coerce').dt.time
    datos = datos.replace('', None).astype(object).where(pd.notna(datos), None)
    columnas_sql = sql.SQL(', ').join(map(sql.Identifier, COLUMNAS))
    definiciones = sql.SQL(', ').join(sql.SQL('{} {}').format(sql.Identifier(n), sql.SQL(t)) for _, n, t in CAMPOS)
    print(f'Cargando {len(datos)} registros a Neon...', flush=True)
    # Reemplazo transaccional de un historico completo, sin deduplicar por orden.
    # Si falla COPY, PostgreSQL revierte el reemplazo.
    with psycopg.connect(DATABASE_URL, connect_timeout=20) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} ({}, fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT NOW())').format(TABLA, definiciones))
            cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(TABLA))
            with cursor.copy(sql.SQL('COPY {} ({}) FROM STDIN').format(TABLA, columnas_sql)) as copia:
                for fila in datos.itertuples(index=False, name=None):
                    copia.write_row(fila)
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS iw47_orden_idx ON {} (orden)').format(TABLA))
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS iw47_notificacion_idx ON {} (notificacion)').format(TABLA))
    resultado = {'ok': True, 'mensaje': 'Sincronizacion IW47 completada', 'procesados': len(datos), 'segundos': round(time.perf_counter()-inicio, 2)}
    print(resultado, flush=True)
    return resultado


@app.get('/api/estado')
def estado():
    return jsonify(ok=True, servicio='API SAP IW47 - Neon')


@app.post('/api/iw47/actualizar')
def actualizar_iw47():
    if not bloqueo.acquire(blocking=False):
        return jsonify(ok=False, mensaje='Ya existe una carga en proceso'), 409
    try:
        return jsonify(actualizar_neon()), 200
    except Exception as error:
        print('ERROR:', type(error).__name__, str(error), flush=True)
        return jsonify(ok=False, error=type(error).__name__, mensaje='Error durante la sincronizacion; revise la terminal'), 500
    finally:
        bloqueo.release()


if __name__ == '__main__':
    print('API SAP IW47 - NEON | Puerto 5003', flush=True)
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True, use_reloader=False)
