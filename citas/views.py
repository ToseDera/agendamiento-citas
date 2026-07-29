from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from usuarios.decorators import admin_required

from .forms import EspecialidadForm
from .models import Especialidad


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
        especialidad.activa = not especialidad.activa
        especialidad.save(update_fields=['activa'])
        estado = 'activada' if especialidad.activa else 'desactivada'
        messages.success(request, f'Especialidad {estado} correctamente.')
    return redirect('panel_especialidades')
