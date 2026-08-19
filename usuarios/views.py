from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from citas.services import citas_confirmadas_futuras, citas_historial, contar_citas_por_estado

from .decorators import admin_required, es_administrador, es_medico
from .forms import DatosPersonalesForm, HorarioMedicoForm, MedicoEditForm, RegistroForm, RegistroMedicoForm
from .models import Medico, Usuario
from .services import registrar_cambios_datos_personales, snapshot_datos_personales

CAMPOS_PROPIOS = ('correo', 'telefono', 'fecha_nacimiento')
CAMPOS_ADMIN_PROPIOS = ('nombre', 'apellido', 'correo', 'telefono', 'fecha_nacimiento', 'tipo_documento')
CAMPOS_ADMIN_SOBRE_PACIENTE = ('nombre', 'apellido', 'correo', 'telefono', 'fecha_nacimiento', 'tipo_documento')
CAMPOS_DATOS_PERSONALES_MEDICO = ('nombre', 'apellido', 'correo', 'telefono', 'fecha_nacimiento', 'tipo_documento')


def _editar_datos_personales(request, instance, campos_editables, realizado_por):
    """Mecanismo compartido (decisión 3): construye DatosPersonalesForm con
    el subconjunto de campos que el llamador autoriza, y si guarda, audita
    cada campo que cambió comparando contra el estado ANTES de bindear el
    form. El llamador (la vista) decide el redirect/mensaje — acá solo se
    resuelve qué es editable y qué se audita."""
    if request.method != 'POST':
        return DatosPersonalesForm(instance=instance, campos_editables=campos_editables), False

    form = DatosPersonalesForm(request.POST, instance=instance, campos_editables=campos_editables)
    valores_antes = snapshot_datos_personales(instance, campos_editables)
    if form.is_valid():
        usuario = form.save()
        registrar_cambios_datos_personales(usuario, valores_antes, realizado_por=realizado_por)
        return form, True
    return form, False


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


@login_required
def perfil(request):
    """HU-23 (fase 7, decisión 7: revierte el solo-lectura de la fase 6b).
    Editable — correo, teléfono y fecha de nacimiento — para los tres roles.
    El administrador, desde fase 7.1 decisión 3, edita además nombre,
    apellido y tipo de documento: ya edita esos mismos datos de terceros,
    así que negárselos sobre sí mismo sería incoherente. es_administrador
    es el mismo mecanismo de roles que ya decide accesos en todo el resto
    de la app (admin_required, el context processor de roles). El número de
    documento sigue intocable para los tres roles, el administrador
    incluido — DatosPersonalesForm nunca lo declara como campo."""
    campos = CAMPOS_ADMIN_PROPIOS if es_administrador(request.user) else CAMPOS_PROPIOS
    form, guardado = _editar_datos_personales(
        request, request.user, campos, realizado_por=request.user,
    )
    if guardado:
        messages.success(request, 'Perfil actualizado correctamente.')
        return redirect('perfil')
    return render(request, 'usuarios/perfil.html', {'form': form})


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
        valores_antes = snapshot_datos_personales(medico.usuario, CAMPOS_DATOS_PERSONALES_MEDICO)
        if form.is_valid():
            usuario = form.save()
            registrar_cambios_datos_personales(usuario, valores_antes, realizado_por=request.user)
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
        elif not medico.especialidad.activa:
            messages.error(
                request,
                'No se puede reactivar: la especialidad de este médico está inactiva. '
                'Reactiva primero la especialidad y luego reactiva al médico.',
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


# --- Fase 6c: ficha de solo lectura del paciente (HU-18 a HU-20) -------------
#
# Vistas nuevas y separadas de las del propio paciente (decisión 3): el
# paciente de mis_citas/mis_citas_historial sale siempre de request.user, y
# estas de acá salen siempre de un Usuario resuelto por número de documento
# dentro de una vista @admin_required — nunca de un parámetro que el
# paciente pudiera manipular. _paciente_o_404 exige pertenencia al grupo
# Paciente para que la cédula de un médico o de un administrador nunca
# resuelva una ficha (decisión 2), tanto en la búsqueda como si alguien
# arma a mano la URL de una ficha con otro pk.

def _paciente_o_404(pk):
    return get_object_or_404(
        Usuario.objects.select_related('tipo_documento'), pk=pk, groups__name='Paciente',
    )


@admin_required
def panel_pacientes(request):
    """HU-18: búsqueda por número de documento, coincidencia exacta
    (decisión 2) — no hay padrón navegable de pacientes. Un número que no
    pertenece a ningún paciente (no existe, o pertenece a un médico o
    administrador) da el mismo "no encontrado", sin distinguir el motivo."""
    numero_documento = request.GET.get('numero_documento', '').strip()
    buscado = bool(numero_documento)
    paciente = None
    if buscado:
        paciente = Usuario.objects.select_related('tipo_documento').filter(
            numero_documento=numero_documento, groups__name='Paciente',
        ).first()
    return render(request, 'panel/paciente_buscar.html', {
        'numero_documento': numero_documento, 'paciente': paciente, 'buscado': buscado,
    })


@admin_required
def panel_paciente_ficha(request, pk):
    paciente = _paciente_o_404(pk)
    conteos = contar_citas_por_estado(paciente)
    return render(request, 'panel/paciente_ficha.html', {
        'paciente': paciente, 'tab_activa': 'ficha',
        'conteo_confirmada': conteos['confirmada'],
        'conteo_atendida': conteos['atendida'],
        'conteo_cancelada': conteos['cancelada'],
        'conteo_no_atendida': conteos['no atendida'],
    })


@admin_required
def panel_paciente_editar(request, pk):
    """HU-18 (fase 7, tarea 4): el administrador edita todos los campos de
    la columna "Admin sobre otros" de la decisión 1 — nombre, apellido,
    correo, teléfono, fecha de nacimiento y tipo de documento. numero_documento
    nunca es un campo de DatosPersonalesForm, así que no hay forma de que un
    POST lo cambie."""
    paciente = _paciente_o_404(pk)
    form, guardado = _editar_datos_personales(
        request, paciente, CAMPOS_ADMIN_SOBRE_PACIENTE, realizado_por=request.user,
    )
    if guardado:
        messages.success(request, 'Datos del paciente actualizados correctamente.')
        return redirect('panel_paciente_ficha', pk=paciente.pk)
    return render(request, 'panel/paciente_editar.html', {
        'paciente': paciente, 'form': form, 'tab_activa': 'ficha',
    })


@admin_required
def panel_paciente_citas(request, pk):
    paciente = _paciente_o_404(pk)
    citas = citas_confirmadas_futuras(paciente)
    return render(request, 'panel/paciente_citas.html', {
        'paciente': paciente, 'tab_activa': 'citas', 'citas': citas,
    })


@admin_required
def panel_paciente_historial(request, pk):
    paciente = _paciente_o_404(pk)
    citas = citas_historial(paciente)
    return render(request, 'panel/paciente_historial.html', {
        'paciente': paciente, 'tab_activa': 'historial', 'citas': citas,
    })
