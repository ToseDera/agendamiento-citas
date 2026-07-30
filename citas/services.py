"""
Motor de disponibilidad de horarios médicos.

No contiene lógica de vistas ni de formularios: dado un médico y un rango de
fechas, calcula qué slots (fecha + hora_inicio + hora_fin) están libres para
agendar, aplicando en orden:
  1. Los bloques recurrentes de HorarioMedico para el día de la semana.
  2. Partición de cada bloque en intervalos de Especialidad.duracion_cita_min
     (el remanente que no alcanza a formar un intervalo completo se descarta).
  3. Exclusión de los slots cubiertos por una ExcepcionHorario (día completo
     o rango puntual).
  4. Exclusión de los slots ya ocupados por una Cita que no esté cancelada
     (Cita.ocupa_slot=True).
  5. Exclusión de slots cuya fecha/hora ya pasó.

Ventana de agendamiento por defecto: hoy hasta VENTANA_AGENDAMIENTO_DIAS días
adelante (configurable llamando a obtener_slots_disponibles con fecha_desde/
fecha_hasta explícitos).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from .models import Cita, CitaLog, EstadoCita, ExcepcionHorario

VENTANA_AGENDAMIENTO_DIAS = 30


@dataclass(frozen=True)
class Slot:
    fecha: date
    hora_inicio: time
    hora_fin: time


def sumar_minutos(hora, minutos):
    referencia = datetime.combine(date.today(), hora)
    return (referencia + timedelta(minutes=minutos)).time()


def _particionar_bloque(hora_inicio, hora_fin, duracion_min):
    intervalos = []
    inicio_actual = hora_inicio
    while True:
        fin_actual = sumar_minutos(inicio_actual, duracion_min)
        if fin_actual > hora_fin:
            break
        intervalos.append((inicio_actual, fin_actual))
        inicio_actual = fin_actual
    return intervalos


def _dia_bloqueado_por_excepcion(excepciones_del_dia):
    return any(exc.hora_inicio is None for exc in excepciones_del_dia)


def _slot_pisado_por_excepcion(slot_inicio, slot_fin, excepciones_del_dia):
    for excepcion in excepciones_del_dia:
        if excepcion.hora_inicio is None:
            continue
        if excepcion.hora_inicio < slot_fin and excepcion.hora_fin > slot_inicio:
            return True
    return False


def obtener_slots_disponibles(medico, fecha_desde=None, fecha_hasta=None):
    """Devuelve una lista de Slot libres para el médico, ordenados por fecha y hora."""
    ahora = timezone.localtime()
    hoy = ahora.date()
    fecha_desde = fecha_desde or hoy
    fecha_hasta = fecha_hasta or (hoy + timedelta(days=VENTANA_AGENDAMIENTO_DIAS))

    duracion_min = medico.especialidad.duracion_cita_min

    horarios_por_dia = {}
    for horario in medico.horarios.all():
        horarios_por_dia.setdefault(horario.dia_semana, []).append(horario)

    excepciones_por_fecha = {}
    for excepcion in ExcepcionHorario.objects.filter(
        medico=medico, fecha__gte=fecha_desde, fecha__lte=fecha_hasta,
    ):
        excepciones_por_fecha.setdefault(excepcion.fecha, []).append(excepcion)

    ocupados_por_fecha = {}
    for cita in Cita.objects.filter(
        medico=medico, fecha__gte=fecha_desde, fecha__lte=fecha_hasta, ocupa_slot=True,
    ):
        ocupados_por_fecha.setdefault(cita.fecha, set()).add(cita.hora_inicio)

    slots_disponibles = []
    fecha_actual = fecha_desde
    while fecha_actual <= fecha_hasta:
        horarios_del_dia = horarios_por_dia.get(fecha_actual.weekday(), [])
        excepciones_del_dia = excepciones_por_fecha.get(fecha_actual, [])

        if horarios_del_dia and not _dia_bloqueado_por_excepcion(excepciones_del_dia):
            ocupados = ocupados_por_fecha.get(fecha_actual, set())
            for horario in horarios_del_dia:
                for hora_inicio, hora_fin in _particionar_bloque(
                    horario.hora_inicio, horario.hora_fin, duracion_min,
                ):
                    if hora_inicio in ocupados:
                        continue
                    if _slot_pisado_por_excepcion(hora_inicio, hora_fin, excepciones_del_dia):
                        continue
                    if fecha_actual == hoy and hora_inicio <= ahora.time():
                        continue
                    slots_disponibles.append(Slot(fecha_actual, hora_inicio, hora_fin))

        fecha_actual += timedelta(days=1)

    return slots_disponibles


def cambiar_estado_cita(cita, nombre_estado, realizado_por, detalle=''):
    """
    Único punto autorizado para transicionar el estado de una Cita.

    Actualiza vía cita.save() (nunca con queryset.update() ni bulk_update,
    ver el docstring de Cita.ocupa_slot) y registra el CitaLog de auditoría
    en la misma transacción.
    """
    nuevo_estado = EstadoCita.objects.get(nombre=nombre_estado)
    with transaction.atomic():
        cita.estado = nuevo_estado
        cita.save()
        CitaLog.objects.create(
            cita=cita, estado=nuevo_estado, accion=nombre_estado,
            realizado_por=realizado_por, detalle=detalle,
        )
    return cita