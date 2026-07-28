from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import TipoDocumento, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = (
        'username', 'numero_documento', 'email', 'first_name', 'last_name',
        'group_list', 'is_staff',
    )
    list_filter = UserAdmin.list_filter + ('tipo_documento',)
    fieldsets = UserAdmin.fieldsets + (
        ('Datos de MedSync', {
            'fields': ('tipo_documento', 'numero_documento', 'fecha_nacimiento', 'telefono'),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Datos de MedSync', {
            'fields': ('tipo_documento', 'numero_documento', 'fecha_nacimiento', 'telefono', 'email'),
        }),
    )

    @admin.display(description='Grupos')
    def group_list(self, obj):
        return ', '.join(group.name for group in obj.groups.all())


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre')
    search_fields = ('nombre',)
