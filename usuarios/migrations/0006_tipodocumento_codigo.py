from django.db import migrations, models

CODIGOS_POR_NOMBRE = {
    'Cédula de Ciudadanía (CC)': 'CC',
    'Tarjeta de Identidad (TI)': 'TI',
    'Cédula de Extranjería (CE)': 'CE',
    'Permiso Especial (PPT)': 'PPT',
    'Pasaporte (PA)': 'PA',
}


def poblar_codigos(apps, schema_editor):
    TipoDocumento = apps.get_model('usuarios', 'TipoDocumento')

    for nombre, codigo in CODIGOS_POR_NOMBRE.items():
        TipoDocumento.objects.filter(nombre=nombre).update(codigo=codigo)

    # Cualquier tipo agregado manualmente (admin) que no esté en el mapeo:
    # le damos un código derivado del nombre para no dejar filas sin poblar.
    sin_codigo = TipoDocumento.objects.filter(codigo='')
    for tipo in sin_codigo:
        tipo.codigo = f'TIPO{tipo.pk}'
        tipo.save(update_fields=['codigo'])


def vaciar_codigos(apps, schema_editor):
    TipoDocumento = apps.get_model('usuarios', 'TipoDocumento')
    TipoDocumento.objects.update(codigo='')


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0005_renombrar_y_agregar_tipos_documento'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='codigo',
            field=models.CharField(default='', max_length=10, blank=True),
            preserve_default=False,
        ),
        migrations.RunPython(poblar_codigos, vaciar_codigos),
        migrations.AlterField(
            model_name='tipodocumento',
            name='codigo',
            field=models.CharField(max_length=10, unique=True),
        ),
    ]
