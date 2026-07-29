from django.contrib import admin

from .models import Especialidad, HorarioMedico


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
