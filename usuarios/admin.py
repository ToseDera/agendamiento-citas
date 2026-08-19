from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CambioUsuarioLog, Medico, TipoDocumento, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    """El campo `username` (la cédula) sigue editable acá porque UserAdmin
    lo trae de fábrica y este admin no lo quita. La aplicación nunca permite
    editarlo — es la fase 7.1, decisión 5b: el admin de Django, reservado
    para superusuarios, es la única puerta por la que esa regla se puede
    violar. Es una limitación conocida y aceptada, no un descuido."""

    list_display = (
        'username', 'numero_documento', 'email', 'first_name', 'last_name',
        'group_list', 'is_staff',
    )
    list_filter = UserAdmin.list_filter + ('tipo_documento',)
    fieldsets = UserAdmin.fieldsets + (
        ('Datos de MediClick', {
            'fields': ('tipo_documento', 'numero_documento', 'fecha_nacimiento', 'telefono'),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Datos de MediClick', {
            'fields': ('tipo_documento', 'numero_documento', 'fecha_nacimiento', 'telefono', 'email'),
        }),
    )

    @admin.display(description='Grupos')
    def group_list(self, obj):
        return ', '.join(group.name for group in obj.groups.all())


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'codigo')
    search_fields = ('nombre', 'codigo')


@admin.register(Medico)
class MedicoAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'especialidad', 'registro_medico', 'activo')
    list_filter = ('especialidad', 'activo')
    search_fields = ('usuario__numero_documento', 'usuario__first_name', 'usuario__last_name')
    autocomplete_fields = ('usuario',)


@admin.register(CambioUsuarioLog)
class CambioUsuarioLogAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'campo', 'valor_anterior', 'valor_nuevo', 'realizado_por_display', 'fecha_creacion')
    list_filter = ('campo',)
    search_fields = ('usuario__numero_documento', 'usuario__first_name', 'usuario__last_name')
    fieldsets = (
        (None, {
            'fields': ('usuario', 'campo', 'valor_anterior', 'valor_nuevo', 'realizado_por', 'fecha_creacion'),
            'description': (
                'Este registro solo cubre ediciones hechas desde la aplicación (perfil, ficha del '
                'paciente, formulario de médicos). Una edición hecha directamente desde este admin de '
                'Django — incluido un cambio del nombre de usuario (la cédula) por un superusuario en '
                'Usuarios — no queda registrada acá.'
            ),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Realizado por')
    def realizado_por_display(self, obj):
        return obj.realizado_por or 'Sistema (automático)'
