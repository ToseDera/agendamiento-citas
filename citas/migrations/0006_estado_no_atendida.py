from django.db import migrations

ESTADOS = ['no atendida']


def crear_estados(apps, schema_editor):
    EstadoCita = apps.get_model('citas', 'EstadoCita')
    for nombre in ESTADOS:
        EstadoCita.objects.get_or_create(nombre=nombre)


def eliminar_estados(apps, schema_editor):
    EstadoCita = apps.get_model('citas', 'EstadoCita')
    EstadoCita.objects.filter(nombre__in=ESTADOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('citas', '0005_estados_cita_iniciales'),
    ]

    operations = [
        migrations.RunPython(crear_estados, eliminar_estados),
    ]
