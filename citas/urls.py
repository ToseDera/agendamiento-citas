from django.urls import path

from . import views

urlpatterns = [
    path('panel/especialidades/', views.especialidad_list, name='panel_especialidades'),
    path('panel/especialidades/nueva/', views.especialidad_create, name='panel_especialidad_nueva'),
    path('panel/especialidades/<int:pk>/editar/', views.especialidad_edit, name='panel_especialidad_editar'),
    path('panel/especialidades/<int:pk>/toggle/', views.especialidad_toggle, name='panel_especialidad_toggle'),
    path('panel/citas/', views.panel_citas, name='panel_citas'),
    path('panel/citas/<int:pk>/cancelar/', views.panel_citas_cancelar, name='panel_citas_cancelar'),

    path('agendar/', views.agendar_especialidades, name='agendar_especialidades'),
    path('agendar/especialidades/<int:especialidad_pk>/medicos/', views.agendar_medicos, name='agendar_medicos'),
    path('agendar/medicos/<int:medico_pk>/horarios/', views.agendar_slots, name='agendar_slots'),
    path('agendar/medicos/<int:medico_pk>/confirmar/', views.agendar_confirmar, name='agendar_confirmar'),

    path('mis-citas/', views.mis_citas, name='mis_citas'),
    path('mis-citas/historial/', views.mis_citas_historial, name='mis_citas_historial'),
    path('mis-citas/<int:pk>/', views.mis_citas_detalle, name='mis_citas_detalle'),
    path('mis-citas/<int:pk>/cancelar/', views.mis_citas_cancelar, name='mis_citas_cancelar'),

    path('mi-agenda/', views.medico_agenda, name='medico_agenda'),
    path('mi-agenda/<int:pk>/', views.medico_cita_detalle, name='medico_cita_detalle'),
    path('mi-agenda/<int:pk>/atendida/', views.medico_marcar_atendida, name='medico_marcar_atendida'),
    path('mi-agenda/<int:pk>/comentario/', views.medico_comentario, name='medico_comentario'),
    path('mi-horario/', views.medico_mi_horario, name='medico_mi_horario'),
    path(
        'mi-horario/<int:horario_pk>/eliminar/',
        views.medico_mi_horario_eliminar, name='medico_mi_horario_eliminar',
    ),
    path('mis-excepciones/', views.medico_mis_excepciones, name='medico_mis_excepciones'),
    path(
        'mis-excepciones/<int:excepcion_pk>/eliminar/',
        views.medico_mi_excepcion_eliminar, name='medico_mi_excepcion_eliminar',
    ),
]