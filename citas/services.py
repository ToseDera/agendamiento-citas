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
from django.db.models import Count, Q
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


def fusionar_slots_contiguos(slots):
    """
    Funde slots libres consecutivos (fin de uno == inicio del siguiente) en
    un solo Slot por rango continuo. El calendario es una vista de overview:
    una caja por slot de 20 min es ruido, no información.

    Agrupa por fecha antes de comparar contigüidad: sin eso, un slot que
    termina 23:59 de un día y otro que empieza 00:00 del día siguiente
    comparten minuto-de-medianoche por coincidencia de reloj y se fundirían
    a través de la medianoche, que es exactamente el tipo de error que
    _particionar_bloque ya tuvo que blindar en minutos-desde-medianoche.
    """
    slots_por_fecha = {}
    for slot in slots:
        slots_por_fecha.setdefault(slot.fecha, []).append(slot)

    fusionados = []
    for fecha in sorted(slots_por_fecha):
        ordenados = sorted(slots_por_fecha[fecha], key=lambda s: s.hora_inicio)
        inicio_actual = None
        fin_actual_min = None
        for slot in ordenados:
            inicio_min = slot.hora_inicio.hour * 60 + slot.hora_inicio.minute
            fin_min = slot.hora_fin.hour * 60 + slot.hora_fin.minute
            if fin_actual_min is not None and inicio_min == fin_actual_min:
                fin_actual_min = fin_min
            else:
                if inicio_actual is not None:
                    fusionados.append(Slot(fecha, inicio_actual, time(fin_actual_min // 60, fin_actual_min % 60)))
                inicio_actual = slot.hora_inicio
                fin_actual_min = fin_min
        if inicio_actual is not None:
            fusionados.append(Slot(fecha, inicio_actual, time(fin_actual_min // 60, fin_actual_min % 60)))

    return fusionados


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
    # Agregados sin romper el frozen (van al final, con default): la única
    # forma de conocerlos hoy es leer evento.origen.<campo> desde la
    # plantilla, que es justo lo que este contrato existe para evitar.
    dia_completo: bool = False
    comentario: str | None = None


def _ya_paso(fecha, hora, ahora=None):
    """True si la fecha/hora (hora local, naive) ya quedó en el pasado.

    `ahora` es inyectable (usado por cerrar_citas_vencidas para tests
    deterministas); los llamadores existentes no lo pasan, así que siguen
    comparando contra el reloj real como siempre.
    """
    ahora = ahora or timezone.localtime()
    if fecha < ahora.date():
        return True
    if fecha > ahora.date():
        return False
    return hora <= ahora.time()


def _es_medico_dueno(cita, usuario):
    medico = getattr(usuario, 'medico', None)
    return medico is not None and medico.pk == cita.medico_id


def puede_cancelar_cita(cita, usuario, *, es_admin=None):
    """Decisión 3: paciente dueño o administrador, cita confirmada y futura.

    `es_admin` es un atajo interno: `_evento_ocupacion` lo llama una vez por
    cita dentro del bucle de obtener_eventos_calendario, y sin él cada
    llamada volvería a pagar la consulta de es_administrador() (pertenencia
    a grupo), haciendo crecer el número de consultas del calendario con la
    cantidad de citas. Ningún llamador fuera de ese bucle lo pasa, así que
    su comportamiento no cambia."""
    if cita.estado.nombre != 'confirmada':
        return False
    if _ya_paso(cita.fecha, cita.hora_inicio):
        return False
    if es_admin is None:
        es_admin = es_administrador(usuario)
    if es_admin:
        return True
    return cita.paciente_id == usuario.pk


def puede_marcar_atendida(cita, usuario):
    """Decisión 4 (fase 6b): solo el médico dueño, con el horario ya pasado,
    y confirmada O no atendida — esta última es la corrección de una cita
    que el médico sí atendió pero el cierre automático (cerrar_citas_vencidas)
    ya había marcado como no atendida antes de que la registrara. Queda su
    rastro completo en CitaLog (confirmada → no atendida → atendida)."""
    if cita.estado.nombre not in ('confirmada', 'no atendida'):
        return False
    if not _ya_paso(cita.fecha, cita.hora_fin):
        return False
    return _es_medico_dueno(cita, usuario)


def puede_editar_comentario(cita, usuario):
    """Decisión 5: solo el médico dueño, y solo en citas ya atendidas."""
    if cita.estado.nombre != 'atendida':
        return False
    return _es_medico_dueno(cita, usuario)


def cerrar_citas_vencidas(ahora=None, dry_run=False):
    """
    Fase 6a.1 (HU-17): toda cita 'confirmada' cuya hora de fin ya pasó en
    hora local pasa a 'no atendida'. No cancela ni libera el slot: ocupa_slot
    sigue True (Cita.save() ya trata cualquier estado distinto de 'cancelada'
    como ocupante), así que el turno consumido no vuelve a ofrecerse.

    Reusa _ya_paso (mismo criterio que puede_marcar_atendida/
    puede_cancelar_cita) en vez de reimplementar la comparación
    fecha/hora-de-fin: es una comparación, no una suma, así que no hay riesgo
    de wraparound de medianoche como el que _particionar_bloque sí tuvo que
    blindar.

    `ahora` es inyectable para tests deterministas sin mockear
    timezone.localtime(). `dry_run=True` no escribe nada: solo cuenta.
    """
    ahora = ahora or timezone.localtime()
    candidatas = Cita.objects.filter(
        estado__nombre='confirmada', fecha__lte=ahora.date(),
    ).select_related('estado')
    vencidas = [cita for cita in candidatas if _ya_paso(cita.fecha, cita.hora_fin, ahora=ahora)]

    if dry_run:
        return len(vencidas)

    for cita in vencidas:
        cambiar_estado_cita(
            cita, 'no atendida', realizado_por=None,
            detalle='Cierre automático: la hora de fin de la cita ya pasó.',
        )
    return len(vencidas)


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
        dia_completo=dia_completo,
    )


def _evento_ocupacion(cita, usuario, es_admin):
    """`es_admin` lo calcula una sola vez obtener_eventos_calendario (no una
    vez por cita): es_administrador() es una consulta a la base, y sin este
    atajo el número de consultas del calendario crecería con la cantidad de
    citas (una agenda propia con más citas confirmadas volvería a pagar esa
    consulta por cada una)."""
    acciones = ['ver_detalle']
    if puede_cancelar_cita(cita, usuario, es_admin=es_admin):
        acciones.append('cancelar')
    if puede_marcar_atendida(cita, usuario):
        acciones.append('marcar_atendida')
    if puede_editar_comentario(cita, usuario):
        acciones.append('editar_comentario')

    # panel/citas_list.html (la vista de citas del administrador) nunca
    # mostró motivo_consulta — el calendario no debe ser la excepción.
    if es_admin:
        titulo = f'Cita — {cita.paciente.get_full_name()}'
    else:
        titulo = cita.motivo_consulta or 'Cita médica'

    inicio = timezone.make_aware(datetime.combine(cita.fecha, cita.hora_inicio))
    fin = timezone.make_aware(datetime.combine(cita.fecha, cita.hora_fin))
    return EventoCalendario(
        tipo='ocupacion',
        id=f'ocupacion-{cita.pk}',
        titulo=titulo,
        inicio=inicio, fin=fin, estado=cita.estado.nombre,
        medico=cita.medico, especialidad=cita.medico.especialidad,
        acciones_permitidas=acciones, origen=cita,
        desplazamiento_min=cita.hora_inicio.hour * 60 + cita.hora_inicio.minute,
        comentario=cita.comentario_medico or None,
    )


CAPAS_VALIDAS = frozenset({'disponibilidad', 'bloqueo', 'ocupacion'})


def _alcance_por_rol(usuario, desde, hasta, filtros):
    """
    Médicos y citas visibles para `usuario` en [desde, hasta], con `filtros`
    {'medico_id': ..., 'especialidad_id': ...} aplicados — solo restringen,
    nunca amplían el alcance que el rol ya tiene. También devuelve qué capas
    puede llegar a ver ese rol como máximo: el paciente nunca tuvo
    disponibilidad ni bloqueo, aunque `medicos` vacío ya lo garantizaría por
    sí solo — declararlo explícito no depende de ese efecto colateral.

    Compartido por obtener_eventos_calendario y
    obtener_densidad_citas_por_dia: mismo contrato de rango y filtros para
    las dos, una sola vez la lógica de alcance por rol.
    """
    if es_administrador(usuario):
        medicos = Medico.objects.filter(activo=True).select_related('usuario', 'especialidad')
        citas_qs = Cita.objects.filter(fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True)
        if filtros.get('medico_id'):
            medicos = medicos.filter(pk=filtros['medico_id'])
            citas_qs = citas_qs.filter(medico_id=filtros['medico_id'])
        if filtros.get('especialidad_id'):
            medicos = medicos.filter(especialidad_id=filtros['especialidad_id'])
            citas_qs = citas_qs.filter(medico__especialidad_id=filtros['especialidad_id'])
        capas_permitidas = CAPAS_VALIDAS
    elif es_medico(usuario):
        medicos = Medico.objects.filter(pk=usuario.medico.pk).select_related('usuario', 'especialidad')
        citas_qs = Cita.objects.filter(
            medico=usuario.medico, fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True,
        )
        capas_permitidas = CAPAS_VALIDAS
    else:
        medicos = Medico.objects.none()
        citas_qs = Cita.objects.filter(
            paciente=usuario, fecha__gte=desde, fecha__lte=hasta, ocupa_slot=True,
        )
        capas_permitidas = frozenset({'ocupacion'})
    return medicos, citas_qs, capas_permitidas


def obtener_eventos_calendario(usuario, desde, hasta, filtros=None):
    """
    Devuelve la lista (ordenada por inicio) de EventoCalendario visibles para
    `usuario` entre `desde` y `hasta` (fechas inclusive). El alcance lo decide
    el rol, nunca quien llama a la función (ver _alcance_por_rol).

    `filtros['capas']` (opcional) es un subconjunto de {'disponibilidad',
    'bloqueo', 'ocupacion'}: pide el modo panorámico del administrador. Se
    intersecta con lo que el rol ya permite, nunca lo amplía. El corte es
    real: si 'disponibilidad' no queda en la intersección, ni siquiera se
    entra al bucle que llama a obtener_slots_disponibles por médico — filtrar
    la lista de salida habría dado el mismo resultado visual sin ahorrar
    ninguna consulta.
    """
    filtros = filtros or {}
    medicos, citas_qs, capas_permitidas = _alcance_por_rol(usuario, desde, hasta, filtros)

    capas_pedidas = filtros.get('capas')
    capas = capas_permitidas if capas_pedidas is None else (capas_permitidas & set(capas_pedidas))

    # Materializado una sola vez: se recorre más de una vez abajo (capa de
    # disponibilidad y capa de bloqueo), y en modo panorámico puede ser un
    # padrón de decenas de médicos — no queremos volver a golpear la base
    # por cada recorrida.
    medicos_lista = list(medicos)

    eventos = []
    if 'disponibilidad' in capas:
        for medico in medicos_lista:
            slots = obtener_slots_disponibles(medico, fecha_desde=desde, fecha_hasta=hasta)
            for slot in fusionar_slots_contiguos(slots):
                eventos.append(_evento_disponibilidad(medico, slot))

    if 'bloqueo' in capas:
        # Una sola consulta para todos los médicos, no una por médico: en
        # modo panorámico este es justo el bucle que no puede crecer con el
        # tamaño del padrón (motivo real de filtros['capas'] cortando antes
        # del bucle de disponibilidad, y el mismo problema le pegaba a este).
        medicos_por_pk = {medico.pk: medico for medico in medicos_lista}
        excepciones = ExcepcionHorario.objects.filter(
            medico__in=medicos_lista, fecha__gte=desde, fecha__lte=hasta,
        )
        for excepcion in excepciones:
            eventos.append(_evento_bloqueo(medicos_por_pk[excepcion.medico_id], excepcion))

    if 'ocupacion' in capas:
        citas_qs = citas_qs.select_related('paciente', 'medico__usuario', 'medico__especialidad', 'estado')
        es_admin = es_administrador(usuario)
        for cita in citas_qs:
            eventos.append(_evento_ocupacion(cita, usuario, es_admin))

    eventos.sort(key=lambda evento: evento.inicio)
    return eventos


def obtener_densidad_citas_por_dia(usuario, desde, hasta, filtros=None):
    """
    Conteo de citas por día en [desde, hasta] para `usuario`, con el mismo
    alcance por rol y los mismos `filtros` que obtener_eventos_calendario.
    Una sola consulta agregada (GROUP BY fecha); no instancia Cita ni
    EventoCalendario. `Cita.fecha` ya es un DateField en hora local (no un
    DateTimeField en UTC), así que agrupar por él directamente ya respeta
    America/Bogota sin necesitar TruncDate.

    Pensada para el contador del modo panorámico: ahí no se renderiza cada
    cita, solo cuántas hay — no tiene sentido pagar el costo de traerlas.
    `ocupa_slot=True` ya excluye las canceladas (ver Cita.save()); las
    atendidas sí sostienen ocupa_slot=True y por lo tanto sí se cuentan.
    """
    filtros = filtros or {}
    _medicos, citas_qs, _capas = _alcance_por_rol(usuario, desde, hasta, filtros)
    conteos = citas_qs.values('fecha').annotate(total=Count('id'))
    return {fila['fecha']: fila['total'] for fila in conteos}


# --- Fase 6c: citas de un paciente concreto, vistas desde su propio panel o
# desde la ficha de solo lectura del administrador (HU-18 a HU-20) ----------
#
# citas_confirmadas_futuras/citas_historial reciben `paciente` como
# parámetro explícito, no request.user: las llama tanto mis_citas/
# mis_citas_historial (con request.user) como el panel del administrador
# (con el paciente resuelto por cédula). El control de acceso — que un
# paciente nunca pueda pasar el id de otro, que solo un administrador
# resuelva un paciente por cédula — vive en las vistas que llaman a estas
# funciones, nunca acá: esta capa no sabe quién está mirando, solo arma la
# consulta para el paciente que le dan.

def citas_confirmadas_futuras(paciente):
    ahora = timezone.localtime()
    return Cita.objects.filter(
        paciente=paciente, estado__nombre='confirmada',
    ).filter(
        Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time()),
    ).select_related('medico__usuario', 'medico__especialidad', 'estado').order_by('fecha', 'hora_inicio')


def citas_historial(paciente):
    proximas_ids = citas_confirmadas_futuras(paciente).values('pk')
    return Cita.objects.filter(paciente=paciente).exclude(pk__in=proximas_ids).select_related(
        'medico__usuario', 'medico__especialidad', 'estado',
    ).order_by('-fecha', '-hora_inicio')


ESTADOS_CITA = ('confirmada', 'atendida', 'cancelada', 'no atendida')


def contar_citas_por_estado(paciente):
    """HU-18 (fase 6c, decisión 5): conteo de citas de `paciente` agrupado
    por estado, en una sola consulta agregada (mismo patrón que
    obtener_densidad_citas_por_dia) — no instancia Cita, agrupa por
    estado__nombre. El desglose (y no solo un total) es el punto: cuántas
    "no atendida" tiene un paciente es el dato que le importa al
    administrador al agendarle otra cita."""
    conteos = Cita.objects.filter(paciente=paciente).values('estado__nombre').annotate(total=Count('id'))
    por_estado = {fila['estado__nombre']: fila['total'] for fila in conteos}
    return {nombre: por_estado.get(nombre, 0) for nombre in ESTADOS_CITA}