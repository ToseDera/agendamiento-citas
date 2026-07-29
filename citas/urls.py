from django.urls import path

from . import views

urlpatterns = [
    path('panel/especialidades/', views.especialidad_list, name='panel_especialidades'),
    path('panel/especialidades/nueva/', views.especialidad_create, name='panel_especialidad_nueva'),
    path('panel/especialidades/<int:pk>/editar/', views.especialidad_edit, name='panel_especialidad_editar'),
    path('panel/especialidades/<int:pk>/toggle/', views.especialidad_toggle, name='panel_especialidad_toggle'),
]
