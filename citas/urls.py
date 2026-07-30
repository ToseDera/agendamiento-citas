from django.urls import path

from . import views

urlpatterns = [
    path('panel/especialidades/', views.especialidad_list, name='panel_especialidades'),
    path('panel/especialidades/nueva/', views.especialidad_create, name='panel_especialidad_nueva'),
    path('panel/especialidades/<int:pk>/editar/', views.especialidad_edit, name='panel_especialidad_editar'),
    path('panel/especialidades/<int:pk>/toggle/', views.especialidad_toggle, name='panel_especialidad_toggle'),

    path('agendar/', views.agendar_especialidades, name='agendar_especialidades'),
    path('agendar/especialidades/<int:especialidad_pk>/medicos/', views.agendar_medicos, name='agendar_medicos'),
    path('agendar/medicos/<int:medico_pk>/horarios/', views.agendar_slots, name='agendar_slots'),
    path('agendar/medicos/<int:medico_pk>/confirmar/', views.agendar_confirmar, name='agendar_confirmar'),
]