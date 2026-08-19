"""
Reglas de validación de datos de Usuario compartidas entre formularios
(fase 7.1, decisión 2): vivían duplicadas dentro de RegistroForm.clean() —
acá quedan en un solo lugar para que el registro público y la validación de
un cambio de tipo de documento (ficha del paciente, formulario de médicos,
perfil del administrador) usen exactamente la misma regla.
"""
from datetime import date


def validar_formato_numero_documento(codigo, numero_documento):
    """Reglas de formato por código de TipoDocumento (nunca por su nombre
    visible, que es editable). Devuelve (numero_documento_normalizado, error)
    — error es None cuando es válido; numero_documento_normalizado solo
    difiere del original para Pasaporte (PA), que se guarda en mayúsculas."""
    length = len(numero_documento)
    if codigo == 'CC':
        if not numero_documento.isdigit():
            return numero_documento, 'La Cédula de Ciudadanía solo debe contener números.'
        if not (5 <= length <= 10):
            return numero_documento, 'La Cédula de Ciudadanía debe tener entre 5 y 10 dígitos.'
    elif codigo == 'TI':
        if not numero_documento.isdigit():
            return numero_documento, 'La Tarjeta de Identidad solo debe contener números.'
        if not (10 <= length <= 11):
            return numero_documento, 'La Tarjeta de Identidad debe tener entre 10 y 11 dígitos.'
    elif codigo == 'CE':
        if not numero_documento.isdigit():
            return numero_documento, 'La Cédula de Extranjería solo debe contener números.'
        if not (6 <= length <= 8):
            return numero_documento, 'La Cédula de Extranjería debe tener entre 6 y 8 dígitos.'
    elif codigo == 'PPT':
        if not numero_documento.isdigit():
            return numero_documento, 'El Permiso Especial solo debe contener números.'
        if not (6 <= length <= 8):
            return numero_documento, 'El Permiso Especial debe tener entre 6 y 8 dígitos.'
    elif codigo == 'PA':
        if not numero_documento.isalnum():
            return numero_documento, 'El Pasaporte solo debe contener letras y números.'
        if not (6 <= length <= 16):
            return numero_documento, 'El Pasaporte debe tener entre 6 y 16 caracteres.'
        return numero_documento.upper(), None
    else:
        if not numero_documento.isdigit():
            return numero_documento, 'El número de documento solo debe contener números.'
        if not (5 <= length <= 16):
            return numero_documento, 'El número de documento debe tener entre 5 y 16 dígitos.'
    return numero_documento, None


def mensaje_tipo_documento_incompatible(error_formato):
    return (
        f'{error_formato} El número de documento no se puede editar: si no corresponde '
        'a este tipo, hay que crear la cuenta de nuevo.'
    )


def validar_cambio_tipo_documento(form):
    """Agrega el error a `form` (fase 7.1, decisión 1) cuando el tipo de
    documento cambia respecto al valor original de `form.instance` y el
    número de documento existente — que nunca se edita — no corresponde al
    formato del tipo nuevo. No hace nada si tipo_documento no está en el
    form o no cambió: no hay que bloquear la edición de otros campos por
    datos heredados que ya no fueran estrictamente válidos.

    Se llama desde clean(), antes de que _post_clean() mute la instancia
    con construct_instance(), así que form.instance todavía trae los
    valores de ANTES del POST."""
    tipo_documento = form.cleaned_data.get('tipo_documento')
    if tipo_documento is None or tipo_documento == form.instance.tipo_documento:
        return
    _, error = validar_formato_numero_documento(tipo_documento.codigo, form.instance.numero_documento)
    if error:
        form.add_error('tipo_documento', mensaje_tipo_documento_incompatible(error))


def validar_fecha_nacimiento_no_futura(fecha_nacimiento):
    if fecha_nacimiento > date.today():
        return 'La fecha de nacimiento no puede ser una fecha futura.'
    return None
