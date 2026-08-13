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
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from usuarios.decorators import es_administrador, es_medico
from usuarios.models import Medico

from .models import Cita, CitaLog, EstadoCita, ExcepcionHorario

VENTANA_AGENDAMIENTO_DIAS = 30

# Fase 5: días hacia adelante que muestra la agenda propia del médico
# (distinto de VENTANA_AGENDAMIENTO_DIAS, que es la ventana en la que un
# paciente puede agendar).
VENTANA_AGENDA_MEDICO_DIAS = 14


@dataclass(frozen=True)
class Slot:
    fecha: date
    hora_inicio: time
    hora_fin: time


def sumar_minutos(hora, minutos):
    referencia = datetime.combine(date.today(), hora)
    return (referencia + timedelta(minutes=minutos)).time()


def _particionar_bloque(hora_inicio, hora_fin, duracion_min):
    """
    Trabaja en minutos-desde-medianoche (enteros), no con sumar_minutos():
    sumar_minutos() descarta el día al devolver solo `.time()`, así que un
    bloque cuya duración total es múltiplo exacto de duracion_min (ej.
    11:48-23:48 con citas de 40min: 720/40=18 exacto) hacía que el último
    intervalo aterrizara justo en hora_fin y el siguiente cruzara medianoche
    envuelto a una hora "menor" (23:48 + 40min -> 00:28) — la comparación
    `fin_actual > hora_fin` nunca se cumplía y el bucle no terminaba nunca.
    Con enteros acotados por fin_min no hay wraparound posible.
    """
    inicio_min = hora_inicio.hour * 60 + hora_inicio.minute
    fin_min = hora_fin.hour * 60 + hora_fin.minute

    intervalos = []
    actual_min = inicio_min
    while actual_min + duracion_min <= fin_min:
        fin_actual_min = actual_min + duracion_min
        intervalos.append((
            time(actual_min // 60, actual_min % 60),
            time(fin_actual_min // 60, fin_actual_min % 60),
        ))
        actual_min = fin_actual_min
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


# --- Fase 5: contrato de datos del calendario -------------------------------
#
# obtener_eventos_calendario() es la única función que decide, según el rol
# del usuario, qué eventos le corresponde ver. Devuelve una lista plana de
# EventoCalendario con la misma forma sin importar su origen (disponibilidad,
# bloqueo u ocupación), para que la vista de calendario de la fase siguiente
# la consuma sin tener que conocer HorarioMedico/ExcepcionHorario/Cita por
# separado. En esta fase se consume solo para renderizar listas.


@dataclass(frozen=True)
class EventoCalendario:
    tipo: str  # 'disponibilidad' | 'bloqueo' | 'ocupacion'
    id: str
    titulo: str
    inicio: datetime
    fin: datetime
    estado: str | None
    medico: Medico
    especialidad: object
    acciones_permitidas: list = field(default_factory=list)
    # Instancia origen (Cita/ExcepcionHorario) para que la vista pueda leer
    # datos propios de ese tipo (paciente, motivo_consulta, comentario...)
    # sin ensuciar el contrato normalizado con campos que no aplican a los
    # otros dos tipos de evento. None para 'disponibilidad', que no tiene
    # una fila propia en la base (es derivada, decisión de diseño 8).
    origen: object = None
    desplazamiento_min: int = 0


def _ya_paso(fecha, hora):
    """True si la fecha/hora (hora local, naive) ya quedó en el pasado."""
    ahora = timezone.localtime()
    if fecha < ahora.date():
        return True
    if fecha > ahora.date():
        return False
    return hora <= ahora.time()


def _es_medico_dueno(cita, usuario):
    medico = getattr(usuario, 'medico', None)
    return medico is not None and medico.pk == cita.medico_id


def puede_cancelar_cita(cita, usuario):
    """Decisión 3: paciente dueño o administrador, cita confirmada y futura."""
    if cita.estado.nombre != 'confirmada':
        return False
    if _ya_paso(cita.fecha, cita.hora_inicio):
        return False
    if es_administrador(usuario):
        return True
    return cita.paciente_id == usuario.pk


def puede_marcar_atendida(cita, usuario):
    """Decisión 4: solo el médico dueño, confirmada y con el horario ya pasado."""
    if cita.estado.nombre != 'confirmada':
        return False
    if not _ya_paso(cita.fecha, cita.hora_fin):
        return False
    return _es_medico_dueno(cita, usuario)


def puede_editar_comentario(cita, usuario):
    """Decisión 5: solo el médico dueño, y solo en citas ya atendidas."""
    if cita.estado.nombre != 'atendida':
        return False
    return _es_medico_dueno(cita, usuario)


def _evento_disponibilidad(medico, slot):
    inicio = timezone.make_aware(datetime.combine(slot.fecha, slot.hora_inicio))
    fin = timezone.make_aware(datetime.combine(slot.fecha, slot.hora_fin))
    return EventoCalendario(
        tipo='disponibilidad',
        id=f'disponibilidad-{medico.pk}-{slot.fecha.isoformat()}-{slot.hora_inicio.isoformat()}',
        titulo='Disponible',
        inicio=inicio, fin=fin, estado=None,
        medico=medico, especialidad=medico.especialidad,
        desplazamiento_min=slot.hora_inicio.hour * 60 + slot.hora_inicio.minute,
    )


def _evento_bloqueo(medico, excepcion):
    dia_completo = excepcion.hora_inicio is None
    hora_inicio = time.min if dia_completo else excepcion.hora_inicio
    hora_fin = time.max if dia_completo else excepcion.hora_fin
    inicio = timezone.make_aware(datetime.combine(excepcion.fecha, hora_inicio))
    fin = timezone.make_aware(datetime.combine(excepcion.fecha, hora_fin))
    return EventoCalendario(
        tipo='bloqueo',
        id=f'bloqueo-{excepcion.pk}',
        titulo=excepcion.motivo or ('Día no disponible' if dia_completo else 'Bloqueo'),
        inicio=inicio, fin=fin, estado=None,
        medico=medico, especialidad=medico.especialidad,
        origen=excepcion,
        desplazamiento_min=hora_inicio.hour * 60 + hora_inicio.minute,
    )


def _evento_ocupacion(cita, usuario):
    acciones = ['ver_detalle']
    if puede_cancelar_cita(cita, usuario):
        acciones.append('cancelar')
    if puede_marcar_atendida(cita, usuario):
        acciones.append('marcar_atendida')
    if puede_editar_comentario(cita, usuario):
        acciones.append('editar_comentario')

    inicio = timezone.make_aware(datetime.combine(cita.fecha, cita.hora_inicio))
    fin = timezone.make_aware(datetime.combine(cita.fecha, cita.hora_fin))
    return EventoCalendario(
        tipo='ocupacion',
        id=f'ocupacion-{cita.pk}',
        titulo=cita.motivo_consulta or 'Cita médica',
        inicio=inicio, fin=fin, estado=cita.estado.nombre,
        medico=cita.medico, especialidad=cita.medico.especialidad,
        acciones_permitidas=acciones, origen=cita,
        desplazamiento_min=cita.hora_inicio.hour * 60 + cita.hora_inicio.minute,
    )


def obtener_eventos_calendario(usuario, desde, hasta, filtros=None):
    """
    Devuelve la lista (ordenada por inicio) de EventoCalendario visibles para
    `usuario` entre `desde` y `hasta` (fechas inclusive). El alcance lo decide
    el rol, nunca quien llama a la función:
      - Administrador: todos los médicos activos, con `filtros` opcionales
        {'medico_id': ..., 'especialidad_id': ...}.
      - Médico: solo su propia disponibilidad/bloqueos/citas.
      - Cualquier otro usuario autenticado (paciente): solo sus propias citas
        (sin capas de disponibilidad/bloqueo, que no le pertenecen a nadie).
    """
    filtros = filtros or {}

    if es_administrador(usuario):
        medicos = Medico.objects.filter(activo=True).select_related('usuario', 'especialidad')
        if filtros.get('medico_id'):
            medicos = medicos.filter(pk=filtros['medico_id'])
        if filtros.get('especialidad_id'):
            medicos = medicos.filter(especialidad_id=filtros['especialidad_id'])

        citas_qs = Cita.objects.filter(fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True)
        if filtros.get('medico_id'):
            citas_qs = citas_qs.filter(medico_id=filtros['medico_id'])
        if filtros.get('especialidad_id'):
            citas_qs = citas_qs.filter(medico__especialidad_id=filtros['especialidad_id'])
    elif es_medico(usuario):
        medicos = Medico.objects.filter(pk=usuario.medico.pk).select_related('usuario', 'especialidad')
        citas_qs = Cita.objects.filter(
            medico=usuario.medico, fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True,
        )
    else:
        medicos = Medico.objects.none()
        citas_qs = Cita.objects.filter(
            paciente=usuario, fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True,
        )

    eventos = []
    for medico in medicos:
        for slot in obtener_slots_disponibles(medico, fecha_desde=desde, fecha_hasta=hasta):
            eventos.append(_evento_disponibilidad(medico, slot))
        for excepcion in ExcepcionHorario.objects.filter(medico=medico, fecha__gte=desde, fecha__lte=hasta):
            eventos.append(_evento_bloqueo(medico, excepcion))

    citas_qs = citas_qs.select_related('paciente', 'medico__usuario', 'medico__especialidad', 'estado')
    for cita in citas_qs:
        eventos.append(_evento_ocupacion(cita, usuario))

    eventos.sort(key=lambda evento: evento.inicio)
    return eventos