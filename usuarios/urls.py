from django.urls import path

from . import views

urlpatterns = [
    path('registro/', views.registro, name='registro'),
    path('panel/', views.panel_home, name='panel_home'),
    path('panel/medicos/', views.medico_list, name='panel_medicos'),
    path('panel/medicos/nuevo/', views.medico_create, name='panel_medico_nuevo'),
    path('panel/medicos/<int:pk>/editar/', views.medico_edit, name='panel_medico_editar'),
    path('panel/medicos/<int:pk>/toggle/', views.medico_toggle, name='panel_medico_toggle'),
    path('panel/medicos/<int:pk>/horarios/', views.medico_horarios, name='panel_medico_horarios'),
    path(
        'panel/medicos/<int:pk>/horarios/<int:horario_pk>/eliminar/',
        views.medico_horario_eliminar, name='panel_medico_horario_eliminar',
    ),
]
