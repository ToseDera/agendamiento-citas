from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_time

from usuarios.decorators import admin_required
from usuarios.models import Medico

from .forms import ConfirmarCitaForm, EspecialidadForm
from .models import Cita, CitaLog, EstadoCita, Especialidad
from .services import obtener_slots_disponibles, sumar_minutos


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
        return redirect('inicio')

    form = ConfirmarCitaForm(initial={'fecha': fecha, 'hora_inicio': hora_inicio})
    return render(request, 'citas/confirmar.html', {
        'medico': medico, 'fecha': fecha, 'hora_inicio': hora_inicio,
        'hora_fin': hora_fin, 'form': form,
    })