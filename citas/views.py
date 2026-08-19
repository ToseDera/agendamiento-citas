from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from usuarios.decorators import admin_required, medico_required
from usuarios.models import Medico

from .calendario_geometria import PX_POR_MIN, calcular_ventana, generar_cajas_dia
from .forms import ComentarioMedicoForm, ConfirmarCitaForm, EspecialidadForm, ExcepcionHorarioForm
from .models import Cita, CitaLog, EstadoCita, Especialidad
from .services import (
    ESTADOS_CITA,
    VENTANA_AGENDA_MEDICO_DIAS,
    cambiar_estado_cita,
    citas_confirmadas_futuras,
    citas_historial,
    obtener_densidad_citas_por_dia,
    obtener_eventos_calendario,
    obtener_slots_disponibles,
    puede_cancelar_cita,
    puede_editar_comentario,
    puede_marcar_atendida,
    sumar_minutos,
)


@admin_required
def especialidad_list(request):
    especialidades = Especialidad.objects.all()
    return render(request, 'panel/especialidad_list.html', {'especialidades': especialidades})


@admin_required
def especialidad_create(request):
    if request.method == 'POST':
        form = EspecialidadForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Especialidad creada correctamente.')
            return redirect('panel_especialidades')
    else:
        form = EspecialidadForm()
    return render(request, 'panel/especialidad_form.html', {'form': form, 'modo': 'crear'})


@admin_required
def especialidad_edit(request, pk):
    especialidad = get_object_or_404(Especialidad, pk=pk)
    if request.method == 'POST':
        form = EspecialidadForm(request.POST, instance=especialidad)
        if form.is_valid():
            form.save()
            messages.success(request, 'Especialidad actualizada correctamente.')
            return redirect('panel_especialidades')
    else:
        form = EspecialidadForm(instance=especialidad)
    return render(request, 'panel/especialidad_form.html', {'form': form, 'modo': 'editar', 'especialidad': especialidad})


@admin_required
def especialidad_toggle(request, pk):
    especialidad = get_object_or_404(Especialidad, pk=pk)
    if request.method == 'POST':
        if especialidad.activa and especialidad.medicos.filter(activo=True).exists():
            messages.error(
                request, 'No se puede desactivar: hay médicos activos con esta especialidad.',
            )
            return redirect('panel_especialidades')
        especialidad.activa = not especialidad.activa
        especialidad.save(update_fields=['activa'])
        estado = 'activada' if especialidad.activa else 'desactivada'
        messages.success(request, f'Especialidad {estado} correctamente.')
    return redirect('panel_especialidades')


@login_required
def agendar_especialidades(request):
    especialidades = Especialidad.objects.filter(activa=True)
    return render(request, 'citas/especialidades.html', {'especialidades': especialidades})


@login_required
def agendar_medicos(request, especialidad_pk):
    especialidad = get_object_or_404(Especialidad, pk=especialidad_pk, activa=True)
    medicos = Medico.objects.filter(especialidad=especialidad, activo=True).select_related('usuario')
    return render(request, 'citas/medicos.html', {'especialidad': especialidad, 'medicos': medicos})


@login_required
def agendar_slots(request, medico_pk):
    medico = get_object_or_404(Medico, pk=medico_pk, activo=True)
    slots_por_dia = {}
    for slot in obtener_slots_disponibles(medico):
        slots_por_dia.setdefault(slot.fecha, []).append(slot)
    return render(request, 'citas/slots.html', {'medico': medico, 'slots_por_dia': slots_por_dia})


def _parsear_fecha_hora(fecha_str, hora_str):
    try:
        fecha = parse_date(fecha_str)
        hora_inicio = parse_time(hora_str)
    except (TypeError, ValueError):
        return None, None
    if fecha is None or hora_inicio is None:
        return None, None
    return fecha, hora_inicio


@login_required
def agendar_confirmar(request, medico_pk):
    medico = get_object_or_404(Medico, pk=medico_pk, activo=True)
    datos = request.POST if request.method == 'POST' else request.GET
    fecha, hora_inicio = _parsear_fecha_hora(datos.get('fecha'), datos.get('hora_inicio'))

    if fecha is None or hora_inicio is None:
        messages.error(request, 'Datos de horario inválidos.')
        return redirect('agendar_slots', medico_pk=medico.pk)

    hora_fin = sumar_minutos(hora_inicio, medico.especialidad.duracion_cita_min)

    if request.method == 'POST':
        form = ConfirmarCitaForm(request.POST)
        if not form.is_valid():
            return render(request, 'citas/confirmar.html', {
                'medico': medico, 'fecha': fecha, 'hora_inicio': hora_inicio,
                'hora_fin': hora_fin, 'form': form,
            })

        try:
            with transaction.atomic():
                medico_bloqueado = Medico.objects.select_for_update().get(pk=medico.pk)
                slots_vigentes = {
                    (slot.fecha, slot.hora_inicio) for slot in obtener_slots_disponibles(medico_bloqueado)
                }
                if (fecha, hora_inicio) not in slots_vigentes:
                    messages.error(request, 'Ese horario ya no está disponible. Elige otro.')
                    return redirect('agendar_slots', medico_pk=medico.pk)

                estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
                cita = Cita.objects.create(
                    paciente=request.user,
                    medico=medico_bloqueado,
                    estado=estado_confirmada,
                    fecha=fecha,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    motivo_consulta=form.cleaned_data.get('motivo_consulta', ''),
                )
                CitaLog.objects.create(
                    cita=cita, estado=estado_confirmada, accion='creada',
                    realizado_por=request.user, detalle='Cita agendada por el paciente.',
                )
        except IntegrityError:
            messages.error(request, 'Ese horario ya no está disponible. Elige otro.')
            return redirect('agendar_slots', medico_pk=medico.pk)

        messages.success(request, 'Cita agendada correctamente.')
        return redirect('mis_citas')

    form = ConfirmarCitaForm(initial={'fecha': fecha, 'hora_inicio': hora_inicio})
    return render(request, 'citas/confirmar.html', {
        'medico': medico, 'fecha': fecha, 'hora_inicio': hora_inicio,
        'hora_fin': hora_fin, 'form': form,
    })


# --- Panel del paciente (HU-07, HU-08, HU-09) --------------------------------

@login_required
def mis_citas(request):
    citas = citas_confirmadas_futuras(request.user)
    return render(request, 'citas/mis_citas.html', {'citas': citas})


@login_required
def mis_citas_historial(request):
    citas = citas_historial(request.user)
    return render(request, 'citas/mis_citas_historial.html', {'citas': citas})


@login_required
def mis_citas_detalle(request, pk):
    cita = get_object_or_404(
        Cita.objects.select_related('medico__usuario', 'medico__especialidad', 'estado'), pk=pk,
    )
    if cita.paciente_id != request.user.pk:
        raise PermissionDenied
    return render(request, 'citas/mis_citas_detalle.html', {
        'cita': cita, 'puede_cancelar': puede_cancelar_cita(cita, request.user),
    })


@login_required
def mis_citas_cancelar(request, pk):
    cita = get_object_or_404(Cita.objects.select_related('medico__usuario', 'medico__especialidad'), pk=pk)
    if cita.paciente_id != request.user.pk:
        raise PermissionDenied

    if not puede_cancelar_cita(cita, request.user):
        messages.error(request, 'Esta cita ya no se puede cancelar.')
        return redirect('mis_citas_detalle', pk=cita.pk)

    if request.method == 'POST':
        cambiar_estado_cita(cita, 'cancelada', realizado_por=request.user, detalle='Cancelada por el paciente.')
        messages.success(request, 'Cita cancelada correctamente.')
        return redirect('mis_citas')

    return render(request, 'citas/mis_citas_cancelar.html', {'cita': cita})


# --- Panel del médico ---------------------------------------------------------

@medico_required
def medico_agenda(request):
    """Decisión 3 (fase 6b): la agenda es la lista de trabajo del médico —
    solo confirmadas. Atendidas, canceladas y no atendidas se fueron al
    historial (medico_historial). `capas={'ocupacion'}` evita calcular
    disponibilidad y bloqueos que esta lista nunca mostró."""
    medico = request.user.medico
    hoy = timezone.localdate()
    eventos = obtener_eventos_calendario(
        request.user, hoy, hoy + timedelta(days=VENTANA_AGENDA_MEDICO_DIAS),
        filtros={'capas': {'ocupacion'}},
    )
    citas_agenda = [evento for evento in eventos if evento.estado == 'confirmada']
    return render(request, 'citas/medico_agenda.html', {'medico': medico, 'eventos': citas_agenda})


@medico_required
def medico_historial(request):
    """HU-27: espejo de mis_citas_historial (decisión 3) — todo lo que ya
    salió de la agenda (atendida, cancelada, no atendida), sin ventana de
    tiempo, más reciente primero."""
    medico = request.user.medico
    citas = Cita.objects.filter(medico=medico).exclude(estado__nombre='confirmada').select_related(
        'paciente', 'estado',
    ).order_by('-fecha', '-hora_inicio')
    return render(request, 'citas/medico_historial.html', {'medico': medico, 'citas': citas})


@medico_required
def medico_cita_detalle(request, pk):
    cita = get_object_or_404(
        Cita.objects.select_related('paciente', 'medico__usuario', 'medico__especialidad', 'estado'),
        pk=pk, medico=request.user.medico,
    )
    return render(request, 'citas/medico_cita_detalle.html', {
        'cita': cita,
        'puede_marcar_atendida': puede_marcar_atendida(cita, request.user),
        'puede_editar_comentario': puede_editar_comentario(cita, request.user),
    })


@medico_required
def medico_marcar_atendida(request, pk):
    cita = get_object_or_404(Cita, pk=pk, medico=request.user.medico)
    if request.method == 'POST':
        if puede_marcar_atendida(cita, request.user):
            cambiar_estado_cita(
                cita, 'atendida', realizado_por=request.user,
                detalle='Marcada como atendida por el médico.',
            )
            messages.success(request, 'Cita marcada como atendida.')
        else:
            messages.error(request, 'Esta cita no se puede marcar como atendida todavía.')
    return redirect('medico_cita_detalle', pk=cita.pk)


@medico_required
def medico_comentario(request, pk):
    cita = get_object_or_404(Cita, pk=pk, medico=request.user.medico)
    if not puede_editar_comentario(cita, request.user):
        raise PermissionDenied
    if request.method == 'POST':
        form = ComentarioMedicoForm(request.POST, instance=cita)
        if form.is_valid():
            form.save()
            messages.success(request, 'Comentario guardado correctamente.')
            return redirect('medico_cita_detalle', pk=cita.pk)
    else:
        form = ComentarioMedicoForm(instance=cita)
    return render(request, 'citas/medico_comentario.html', {'form': form, 'cita': cita})


@medico_required
def medico_mi_horario(request):
    """Decisión 1 (fase 6b.1, HU-14): el horario recurrente lo escribe solo
    el administrador. El médico lo ve en solo lectura — un POST directo se
    rechaza acá, en la vista, no ocultando el formulario en la plantilla."""
    if request.method == 'POST':
        raise PermissionDenied
    medico = request.user.medico
    horarios = medico.horarios.all()
    return render(request, 'citas/mi_horario.html', {'medico': medico, 'horarios': horarios})


@medico_required
def medico_mi_horario_eliminar(request, horario_pk):
    raise PermissionDenied


@medico_required
def medico_mis_excepciones(request):
    medico = request.user.medico
    if request.method == 'POST':
        form = ExcepcionHorarioForm(request.POST, medico=medico)
        if form.is_valid():
            form.save()
            messages.success(request, 'Excepción registrada correctamente.')
            return redirect('medico_mis_excepciones')
    else:
        form = ExcepcionHorarioForm(medico=medico)
    excepciones = medico.excepciones.all()
    return render(request, 'citas/mis_excepciones.html', {
        'form': form, 'medico': medico, 'excepciones': excepciones,
    })


@medico_required
def medico_mi_excepcion_eliminar(request, excepcion_pk):
    medico = request.user.medico
    excepcion = get_object_or_404(medico.excepciones, pk=excepcion_pk)
    if request.method == 'POST':
        excepcion.delete()
        messages.success(request, 'Excepción eliminada correctamente.')
    return redirect('medico_mis_excepciones')


# --- Cancelación desde el panel del administrador -----------------------------

@admin_required
def panel_citas(request):
    """Fase 6c, decisión 9: listado global de citas del sistema, en cualquier
    estado, con filtro opcional por estado (sin filtro, muestra las cuatro).
    puede_cancelar se resuelve con es_admin=True fijo — el decorador ya
    garantiza que quien pide esto es administrador — para que
    puede_cancelar_cita no vuelva a pagar es_administrador() por cada fila."""
    estado = request.GET.get('estado', '')
    citas_qs = Cita.objects.select_related(
        'paciente', 'medico__usuario', 'medico__especialidad', 'estado',
    ).order_by('-fecha', '-hora_inicio')
    if estado in ESTADOS_CITA:
        citas_qs = citas_qs.filter(estado__nombre=estado)

    citas = list(citas_qs)
    for cita in citas:
        cita.puede_cancelar = puede_cancelar_cita(cita, request.user, es_admin=True)

    return render(request, 'panel/citas_list.html', {
        'citas': citas, 'estado_filtro': estado, 'estados_filtro': ESTADOS_CITA,
    })


@admin_required
def panel_citas_cancelar(request, pk):
    cita = get_object_or_404(Cita, pk=pk)
    if request.method == 'POST':
        if puede_cancelar_cita(cita, request.user):
            cambiar_estado_cita(
                cita, 'cancelada', realizado_por=request.user,
                detalle='Cancelada por el administrador.',
            )
            messages.success(request, 'Cita cancelada correctamente.')
        else:
            messages.error(request, 'Esta cita ya no se puede cancelar.')
    return redirect('panel_citas')


# --- Calendario del administrador (fase 6a: HU-15 parcial, HU-16) ------------
#
# El parcial de plantilla (panel/_calendario.html) no sabe nada de roles ni
# de alcance — eso lo decide cada vista y, más abajo en la pila,
# obtener_eventos_calendario(). medico_calendario y mis_citas_calendario
# (fase 6b, más abajo en este archivo) comparten ese mismo parcial y el
# helper _contexto_calendario_semana definido junto a panel_calendario.

# Únicas acciones que hoy tienen una vista de destino alcanzable desde el rol
# administrador: puede_marcar_atendida/puede_editar_comentario exigen ser el
# médico dueño, así que nunca aparecen en acciones_permitidas para un evento
# que ve un administrador. No hay una página de confirmación de cancelación
# de una sola cita para el administrador (a diferencia de mis_citas_cancelar
# para el paciente): panel_citas_cancelar es solo un <form method="post">
# incrustado en la fila de panel/citas_list.html, sin una vista GET propia
# que revalide una cita puntual. Enlazamos a la lista, no inventamos una
# vista nueva fuera del alcance de esta fase — ver el reporte.
_ETIQUETAS_ACCION_CALENDARIO = {
    'cancelar': {'texto': 'Cancelar cita', 'icono': 'event_busy'},
    'marcar_atendida': {'texto': 'Marcar como atendida', 'icono': 'check_circle'},
    'editar_comentario': {'texto': 'Editar comentario', 'icono': 'edit_note'},
}


def _url_accion_calendario_admin(accion, evento):
    if accion == 'cancelar':
        return reverse('panel_citas')
    return None


def _url_accion_calendario_medico(accion, evento):
    """El médico nunca puede cancelar (puede_cancelar_cita no lo permite), así
    que solo resuelve las dos acciones que sí puede tener: ambas apuntan a
    vistas existentes que revalidan (decisión 7) — no hay una vista de "marcar
    atendida" con GET propio, por eso esa acción cae en el detalle, donde ya
    vive el <form method="post"> real."""
    if accion == 'marcar_atendida':
        return reverse('medico_cita_detalle', args=[evento.origen.pk])
    if accion == 'editar_comentario':
        return reverse('medico_comentario', args=[evento.origen.pk])
    return None


def _url_accion_calendario_paciente(accion, evento):
    if accion == 'cancelar':
        return reverse('mis_citas_cancelar', args=[evento.origen.pk])
    return None


def _serializar_eventos_para_modal(eventos, url_por_accion):
    payload = {}
    for evento in eventos:
        acciones = []
        for accion in evento.acciones_permitidas:
            if accion == 'ver_detalle':
                continue
            etiqueta = _ETIQUETAS_ACCION_CALENDARIO.get(accion)
            url = url_por_accion(accion, evento)
            if not etiqueta or not url:
                continue
            acciones.append({'id': accion, 'texto': etiqueta['texto'], 'icono': etiqueta['icono'], 'url': url})

        payload[evento.id] = {
            'tipo': evento.tipo,
            'titulo': evento.titulo,
            'estado': evento.estado,
            'dia_completo': evento.dia_completo,
            'hora_inicio': timezone.localtime(evento.inicio).strftime('%H:%M'),
            'hora_fin': timezone.localtime(evento.fin).strftime('%H:%M'),
            'medico_nombre': evento.medico.usuario.get_full_name() if evento.medico else None,
            'medico_especialidad': evento.medico.especialidad.nombre if evento.medico else None,
            'comentario': evento.comentario,
            'acciones': acciones,
        }
    return payload


NOMBRES_DIA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
NOMBRES_DIA_CORTO = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM']

# Techo de médicos individuales en la leyenda: por encima de esto, el color
# deja de ser una pista de identidad y pasa a ser solo de agrupación —
# enumerar 15 nombres en una fila no ayuda a nadie. Solo aplica en modo
# panorámico: filtrado a un médico concreto no hay franjas que explicar.
LEYENDA_MAX_MEDICOS = 8


def _medicos_visibles_en_semana(eventos):
    vistos = {}
    for evento in eventos:
        if evento.medico and evento.medico.pk not in vistos:
            vistos[evento.medico.pk] = evento.medico
    return list(vistos.values())


def _hhmm(minutos):
    return f'{minutos // 60:02d}:{minutos % 60:02d}'


def _lunes_de_semana(fecha):
    return fecha - timedelta(days=fecha.weekday())


def _parsear_id(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _querystring_con_semana(get_params, fecha=None):
    qs = get_params.copy()
    if fecha is None:
        qs.pop('semana', None)
    else:
        qs['semana'] = fecha.isoformat()
    return qs.urlencode()


def _agrupar_dias_para_lista_movil(dias, panoramico):
    """Fuera del modo panorámico siempre son los 7 días (el volumen es chico
    y cada uno importa igual). En panorámico, con un padrón grande, la
    mayoría de los días puede no tener ningún bloqueo — repetir 'Sin
    eventos' varias veces seguidas es ruido, así que las rachas de días
    vacíos consecutivos se colapsan en una sola fila resumen. 'Hoy' nunca
    entra en una racha, aunque esté vacío: es la columna de referencia."""
    if not panoramico:
        return [{'es_racha': False, 'dia': dia} for dia in dias]

    filas = []
    racha = []
    for dia in dias:
        if not dia['eventos'] and not dia['es_hoy']:
            racha.append(dia)
            continue
        if racha:
            filas.append({'es_racha': True, 'dias': racha})
            racha = []
        filas.append({'es_racha': False, 'dia': dia})
    if racha:
        filas.append({'es_racha': True, 'dias': racha})
    return filas


def _contexto_calendario_semana(request, usuario, filtros, panoramico, url_por_accion):
    """Fase 6b: lo que panel_calendario armaba en línea, factorizado para que
    medico_calendario y mis_citas_calendario lo reusen sin copiar la
    aritmética de ventana/geometría ni la serialización del modal — comparten
    el mismo servicio (obtener_eventos_calendario/obtener_densidad_citas_por_dia)
    y la misma vista de "semana", solo con `filtros`/`panoramico`/
    `url_por_accion` propios de cada rol. No decide alcance (eso ya lo hizo
    _alcance_por_rol dentro del servicio) ni arma los filtros de especialidad/
    médico, que son exclusivos del administrador."""
    fecha_semana = parse_date(request.GET.get('semana', '')) or timezone.localdate()
    lunes = _lunes_de_semana(fecha_semana)
    domingo = lunes + timedelta(days=6)
    hoy = timezone.localdate()

    eventos = obtener_eventos_calendario(usuario, lunes, domingo, filtros=filtros)
    ventana = calcular_ventana(eventos)

    densidad_por_fecha = None
    if panoramico:
        densidad_por_fecha = obtener_densidad_citas_por_dia(usuario, lunes, domingo, filtros=filtros)

    dias = []
    for offset in range(7):
        fecha_dia = lunes + timedelta(days=offset)
        eventos_dia = sorted(
            (evento for evento in eventos if timezone.localtime(evento.inicio).date() == fecha_dia),
            key=lambda evento: evento.desplazamiento_min,
        )
        dias.append({
            'fecha': fecha_dia,
            'es_hoy': fecha_dia == hoy,
            'nombre_corto': NOMBRES_DIA_CORTO[offset],
            'nombre_completo': NOMBRES_DIA[offset],
            'eventos': eventos_dia,
            'cajas': generar_cajas_dia(eventos_dia, ventana, mostrar_medico=panoramico),
            'densidad': densidad_por_fecha.get(fecha_dia, 0) if densidad_por_fecha is not None else None,
        })

    eventos_json = _serializar_eventos_para_modal(eventos, url_por_accion)
    medicos_visibles_semana = _medicos_visibles_en_semana(eventos) if panoramico else []
    # La leyenda se arma a partir de esto (decisión de fase 6b.1, tarea 5):
    # deriva de lo que la semana realmente trae, no de panoramico/rol. Así el
    # calendario del paciente nunca lista "Disponible" ni "Bloqueo" sin que
    # el parcial tenga que preguntar por el rol — mismo enfoque que ya usaba
    # docs/prototipos/calendario.html (renderLeyenda: tipos.has(...)).
    tipos_visibles = {evento.tipo for evento in eventos}

    # Formateados a string acá, no como float crudo hacia la plantilla:
    # Django interpola números con coma decimal en es-co ("0,0px"), que
    # como valor de un atributo style es CSS inválido y deja el elemento
    # sin posicionar en silencio.
    alto_total_estilo = f'height:{(ventana.fin_min - ventana.inicio_min) * PX_POR_MIN:.2f}px;'
    horas_gutter = [
        {'texto': _hhmm(minuto), 'estilo': f'top:{(minuto - ventana.inicio_min) * PX_POR_MIN:.2f}px;'}
        for minuto in range(ventana.inicio_min, ventana.fin_min + 1, 60)
    ]

    return {
        'dias': dias,
        'filas_movil': _agrupar_dias_para_lista_movil(dias, panoramico),
        'ventana': ventana,
        'ventana_texto': f'{_hhmm(ventana.inicio_min)} – {_hhmm(ventana.fin_min)}',
        'alto_total_estilo': alto_total_estilo,
        'horas_gutter': horas_gutter,
        'panoramico': panoramico,
        'tipos_visibles': tipos_visibles,
        'medicos_visibles_semana': medicos_visibles_semana[:LEYENDA_MAX_MEDICOS],
        'medicos_visibles_total': len(medicos_visibles_semana),
        'medicos_visibles_supera_techo': len(medicos_visibles_semana) > LEYENDA_MAX_MEDICOS,
        'lunes': lunes,
        'domingo': domingo,
        'eventos_json': eventos_json,
        'querystring_semana_anterior': _querystring_con_semana(request.GET, lunes - timedelta(days=7)),
        'querystring_semana_siguiente': _querystring_con_semana(request.GET, lunes + timedelta(days=7)),
        'querystring_hoy': _querystring_con_semana(request.GET, None),
    }


@admin_required
def panel_calendario(request):
    especialidad_id = _parsear_id(request.GET.get('especialidad'))
    medico_id = _parsear_id(request.GET.get('medico'))

    medico_seleccionado = None
    if medico_id is not None:
        medico_seleccionado = Medico.objects.filter(
            pk=medico_id, activo=True,
        ).select_related('usuario', 'especialidad').first()
        if medico_seleccionado is None:
            medico_id = None

    # Decisión 10: gana el médico. obtener_eventos_calendario encadena
    # medico_id y especialidad_id con AND; sin esta corrección, un
    # querystring con un médico de otra especialidad devolvería
    # silenciosamente cero eventos en vez de la agenda de ese médico.
    if medico_seleccionado is not None and especialidad_id is not None:
        if medico_seleccionado.especialidad_id != especialidad_id:
            qs = request.GET.copy()
            qs['especialidad'] = str(medico_seleccionado.especialidad_id)
            return redirect(f"{reverse('panel_calendario')}?{qs.urlencode()}")
    if medico_seleccionado is not None:
        especialidad_id = medico_seleccionado.especialidad_id

    filtros = {}
    if medico_id is not None:
        filtros['medico_id'] = medico_id
    if especialidad_id is not None:
        filtros['especialidad_id'] = especialidad_id

    panoramico = medico_seleccionado is None
    filtros['capas'] = {'bloqueo'} if panoramico else None

    contexto = _contexto_calendario_semana(request, request.user, filtros, panoramico, _url_accion_calendario_admin)

    especialidades = Especialidad.objects.filter(activa=True).order_by('nombre')
    medicos_padron = Medico.objects.filter(activo=True).select_related(
        'usuario', 'especialidad',
    ).order_by('especialidad__nombre', 'usuario__first_name', 'usuario__last_name')

    contexto.update({
        'medico_seleccionado': medico_seleccionado,
        'especialidad_id': especialidad_id,
        'medico_id': medico_id,
        'especialidades': especialidades,
        'medicos_padron': medicos_padron,
    })
    return render(request, 'panel/calendario.html', contexto)


# --- Calendario del médico (fase 6b: HU-21) y del paciente (HU-22) -----------
#
# Mismo parcial, mismo modal, misma geometría que el del administrador
# (decisión 1): nunca "panorámico" (siempre es la agenda de un único médico,
# el propio), sin filtros de especialidad/médico (no aplican cuando el
# alcance ya es una sola persona) y sin selector de semana adicional al ya
# compartido por _contexto_calendario_semana.

@medico_required
def medico_calendario(request):
    medico = request.user.medico
    contexto = _contexto_calendario_semana(
        request, request.user, filtros={}, panoramico=False,
        url_por_accion=_url_accion_calendario_medico,
    )
    contexto['medico'] = medico
    return render(request, 'citas/medico_calendario.html', contexto)


@login_required
def mis_citas_calendario(request):
    contexto = _contexto_calendario_semana(
        request, request.user, filtros={}, panoramico=False,
        url_por_accion=_url_accion_calendario_paciente,
    )
    return render(request, 'citas/mis_citas_calendario.html', contexto)