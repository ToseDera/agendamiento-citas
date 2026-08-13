from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from usuarios.decorators import admin_required, medico_required
from usuarios.forms import HorarioMedicoForm
from usuarios.models import Medico

from .forms import ComentarioMedicoForm, ConfirmarCitaForm, EspecialidadForm, ExcepcionHorarioForm
from .models import Cita, CitaLog, EstadoCita, Especialidad
from .services import (
    VENTANA_AGENDA_MEDICO_DIAS,
    cambiar_estado_cita,
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

def _citas_proximas_del_paciente(paciente):
    ahora = timezone.localtime()
    return Cita.objects.filter(
        paciente=paciente, estado__nombre='confirmada',
    ).filter(
        Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time()),
    ).select_related('medico__usuario', 'medico__especialidad', 'estado').order_by('fecha', 'hora_inicio')


@login_required
def mis_citas(request):
    citas = _citas_proximas_del_paciente(request.user)
    return render(request, 'citas/mis_citas.html', {'citas': citas})


@login_required
def mis_citas_historial(request):
    proximas_ids = _citas_proximas_del_paciente(request.user).values('pk')
    citas = Cita.objects.filter(paciente=request.user).exclude(pk__in=proximas_ids).select_related(
        'medico__usuario', 'medico__especialidad', 'estado',
    ).order_by('-fecha', '-hora_inicio')
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
    medico = request.user.medico
    hoy = timezone.localdate()
    eventos = obtener_eventos_calendario(request.user, hoy, hoy + timedelta(days=VENTANA_AGENDA_MEDICO_DIAS))
    citas_agenda = [evento for evento in eventos if evento.tipo == 'ocupacion']
    return render(request, 'citas/medico_agenda.html', {'medico': medico, 'eventos': citas_agenda})


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
    medico = request.user.medico
    if request.method == 'POST':
        form = HorarioMedicoForm(request.POST, medico=medico)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bloque de horario agregado correctamente.')
            return redirect('medico_mi_horario')
    else:
        form = HorarioMedicoForm(medico=medico)
    horarios = medico.horarios.all()
    return render(request, 'citas/mi_horario.html', {'form': form, 'medico': medico, 'horarios': horarios})


@medico_required
def medico_mi_horario_eliminar(request, horario_pk):
    medico = request.user.medico
    horario = get_object_or_404(medico.horarios, pk=horario_pk)
    if request.method == 'POST':
        horario.delete()
        messages.success(request, 'Bloque de horario eliminado correctamente.')
    return redirect('medico_mi_horario')


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
    hoy = timezone.localdate()
    ahora = timezone.localtime()
    citas = Cita.objects.filter(estado__nombre='confirmada').filter(
        Q(fecha__gt=hoy) | Q(fecha=hoy, hora_inicio__gt=ahora.time()),
    ).select_related('paciente', 'medico__usuario', 'medico__especialidad').order_by('fecha', 'hora_inicio')
    return render(request, 'panel/citas_list.html', {'citas': citas})


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