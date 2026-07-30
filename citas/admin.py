from django.contrib import admin

from .models import Cita, CitaLog, EstadoCita, Especialidad, ExcepcionHorario, HorarioMedico


@admin.register(Especialidad)
class EspecialidadAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'duracion_cita_min', 'activa')
    list_filter = ('activa',)
    search_fields = ('nombre',)


@admin.register(HorarioMedico)
class HorarioMedicoAdmin(admin.ModelAdmin):
    list_display = ('medico', 'dia_semana', 'hora_inicio', 'hora_fin')
    list_filter = ('dia_semana',)
    search_fields = ('medico__usuario__numero_documento', 'medico__usuario__first_name', 'medico__usuario__last_name')


@admin.register(EstadoCita)
class EstadoCitaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre')


@admin.register(ExcepcionHorario)
class ExcepcionHorarioAdmin(admin.ModelAdmin):
    list_display = ('medico', 'fecha', 'hora_inicio', 'hora_fin', 'motivo')
    list_filter = ('fecha',)
    search_fields = ('medico__usuario__numero_documento', 'medico__usuario__first_name', 'medico__usuario__last_name')


@admin.register(Cita)
class CitaAdmin(admin.ModelAdmin):
    list_display = ('paciente', 'medico', 'fecha', 'hora_inicio', 'estado', 'ocupa_slot')
    list_filter = ('estado', 'fecha')
    search_fields = (
        'paciente__numero_documento', 'paciente__first_name', 'paciente__last_name',
        'medico__usuario__numero_documento', 'medico__usuario__first_name', 'medico__usuario__last_name',
    )
    readonly_fields = ('ocupa_slot',)


@admin.register(CitaLog)
class CitaLogAdmin(admin.ModelAdmin):
    list_display = ('cita', 'accion', 'estado', 'realizado_por', 'fecha_creacion')
    list_filter = ('accion', 'estado')
    search_fields = ('cita__paciente__numero_documento', 'cita__medico__usuario__numero_documento')
