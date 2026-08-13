from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import admin_required, es_administrador, es_medico
from .forms import HorarioMedicoForm, MedicoEditForm, RegistroForm, RegistroMedicoForm
from .models import Medico


def registro(request):
    if request.method == 'POST':
        form = RegistroForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cuenta creada correctamente. Ya puedes iniciar sesión.')
            return redirect('login')
    else:
        form = RegistroForm()
    return render(request, 'registration/registro.html', {'form': form})


@login_required
def inicio(request):
    """Punto único de entrada tras el login: reenvía a cada rol a su propio
    espacio (única fuente de esta lógica, no duplicar en otra vista)."""
    if es_administrador(request.user):
        return redirect('panel_home')
    if es_medico(request.user):
        return redirect('medico_agenda')
    return redirect('mis_citas')


@admin_required
def panel_home(request):
    return render(request, 'panel/home.html')


@admin_required
def medico_list(request):
    medicos = Medico.objects.select_related('usuario', 'especialidad').order_by(
        'usuario__first_name', 'usuario__last_name',
    )
    return render(request, 'panel/medico_list.html', {'medicos': medicos})


@admin_required
def medico_create(request):
    if request.method == 'POST':
        form = RegistroMedicoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Médico registrado correctamente.')
            return redirect('panel_medicos')
    else:
        form = RegistroMedicoForm()
    return render(request, 'panel/medico_form.html', {'form': form, 'modo': 'crear'})


@admin_required
def medico_edit(request, pk):
    medico = get_object_or_404(Medico, pk=pk)
    if request.method == 'POST':
        form = MedicoEditForm(request.POST, instance=medico.usuario, medico=medico)
        if form.is_valid():
            form.save()
            messages.success(request, 'Médico actualizado correctamente.')
            return redirect('panel_medicos')
    else:
        form = MedicoEditForm(instance=medico.usuario, medico=medico)
    return render(request, 'panel/medico_form.html', {'form': form, 'modo': 'editar', 'medico': medico})


@admin_required
def medico_toggle(request, pk):
    medico = get_object_or_404(Medico, pk=pk)
    if request.method == 'POST':
        if medico.activo:
            tiene_citas_futuras = medico.citas.filter(
                estado__nombre='confirmada', fecha__gte=timezone.localdate(),
            ).exists()
            if tiene_citas_futuras:
                messages.error(
                    request,
                    'No se puede desactivar: el médico tiene citas confirmadas próximas.',
                )
                return redirect('panel_medicos')

        medico.activo = not medico.activo
        medico.save(update_fields=['activo'])
        medico.usuario.is_active = medico.activo
        medico.usuario.save(update_fields=['is_active'])
        estado = 'activado' if medico.activo else 'desactivado'
        messages.success(request, f'Médico {estado} correctamente.')
    return redirect('panel_medicos')


@admin_required
def medico_horarios(request, pk):
    medico = get_object_or_404(Medico, pk=pk)
    if request.method == 'POST':
        form = HorarioMedicoForm(request.POST, medico=medico)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bloque de horario agregado correctamente.')
            return redirect('panel_medico_horarios', pk=medico.pk)
    else:
        form = HorarioMedicoForm(medico=medico)
    horarios = medico.horarios.all()
    return render(request, 'panel/medico_horarios.html', {
        'form': form, 'medico': medico, 'horarios': horarios,
    })


@admin_required
def medico_horario_eliminar(request, pk, horario_pk):
    medico = get_object_or_404(Medico, pk=pk)
    horario = get_object_or_404(medico.horarios, pk=horario_pk)
    if request.method == 'POST':
        horario.delete()
        messages.success(request, 'Bloque de horario eliminado correctamente.')
    return redirect('panel_medico_horarios', pk=medico.pk)
