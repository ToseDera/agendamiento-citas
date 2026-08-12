from django.db import migrations

RENOMBRES = {
    'Cédula de ciudadanía': 'Cédula de Ciudadanía (CC)',
    'Tarjeta de identidad': 'Tarjeta de Identidad (TI)',
    'Cédula de extranjería': 'Cédula de Extranjería (CE)',
}
TIPOS_NUEVOS = ['Permiso Especial (PPT)', 'Pasaporte (PA)']


def renombrar_y_agregar_tipos_documento(apps, schema_editor):
    TipoDocumento = apps.get_model('usuarios', 'TipoDocumento')

    for nombre_viejo, nombre_nuevo in RENOMBRES.items():
        actualizados = TipoDocumento.objects.filter(nombre=nombre_viejo).update(nombre=nombre_nuevo)
        if not actualizados:
            # Ya renombrado en una corrida anterior, o base nueva sin el nombre viejo.
            TipoDocumento.objects.get_or_create(nombre=nombre_nuevo)

    for nombre in TIPOS_NUEVOS:
        TipoDocumento.objects.get_or_create(nombre=nombre)


def revertir_renombrado_y_tipos_nuevos(apps, schema_editor):
    TipoDocumento = apps.get_model('usuarios', 'TipoDocumento')

    for nombre_viejo, nombre_nuevo in RENOMBRES.items():
        TipoDocumento.objects.filter(nombre=nombre_nuevo).update(nombre=nombre_viejo)

    TipoDocumento.objects.filter(nombre__in=TIPOS_NUEVOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0004_medico'),
    ]

    operations = [
        migrations.RunPython(renombrar_y_agregar_tipos_documento, revertir_renombrado_y_tipos_nuevos),
    ]
