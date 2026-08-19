"""
Auditoría de cambios sobre datos de Usuario (fase 7, decisión 5): un
CambioUsuarioLog por campo efectivamente modificado. No decide permisos ni
qué campos puede editar cada rol — eso lo resuelve la vista al construir
`campos_editables` para DatosPersonalesForm/MedicoEditForm; esta capa solo
compara el estado de antes contra el de ahora y registra lo que cambió.
"""
from .models import CambioUsuarioLog

CAMPOS_DATOS_PERSONALES = {
    'nombre': 'first_name',
    'apellido': 'last_name',
    'correo': 'email',
    'telefono': 'telefono',
    'fecha_nacimiento': 'fecha_nacimiento',
    'tipo_documento': 'tipo_documento',
}


def snapshot_datos_personales(usuario, campos_editables):
    """Estado ANTES de guardar. Hay que llamarla antes de form.is_valid():
    BaseModelForm._post_clean ya llama a construct_instance ahí, que muta
    la instancia en memoria aunque todavía no se haya guardado nada."""
    return {
        CAMPOS_DATOS_PERSONALES[campo]: getattr(usuario, CAMPOS_DATOS_PERSONALES[campo])
        for campo in campos_editables
    }


def registrar_cambios_datos_personales(usuario, valores_antes, realizado_por):
    for atributo, valor_anterior in valores_antes.items():
        valor_nuevo = getattr(usuario, atributo)
        if valor_anterior != valor_nuevo:
            CambioUsuarioLog.objects.create(
                usuario=usuario, campo=atributo,
                valor_anterior='' if valor_anterior is None else str(valor_anterior),
                valor_nuevo='' if valor_nuevo is None else str(valor_nuevo),
                realizado_por=realizado_por,
            )
