from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from citas.models import Especialidad
from usuarios.forms import RegistroMedicoForm
from usuarios.models import TipoDocumento, Usuario

ADMIN_CEDULA = '9999999999'
ADMIN_PASSWORD = 'admin123'
PACIENTE_CEDULA = '8888888888'
PACIENTE_PASSWORD = 'paciente123'
MEDICO_CEDULA = '7777777777'
MEDICO_PASSWORD = 'medico123'


class Command(BaseCommand):
    help = (
        'Crea datos de desarrollo: un superusuario admin (grupo Administrador) '
        'y, opcionalmente, un paciente y un médico de prueba. Idempotente. '
        'Solo puede correr con DEBUG=True.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sin-extra', action='store_true',
            help='No crear el paciente ni el médico de prueba, solo el admin.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('seed_dev solo puede ejecutarse con DEBUG=True.')

        tipo_documento = TipoDocumento.objects.filter(nombre='Cédula de ciudadanía').first()
        if tipo_documento is None:
            raise CommandError('No existe el catálogo TipoDocumento. Corre "migrate" primero.')

        self._crear_admin(tipo_documento)

        if not options['sin_extra']:
            self._crear_paciente(tipo_documento)
            self._crear_medico(tipo_documento)

    def _crear_admin(self, tipo_documento):
        if Usuario.objects.filter(username=ADMIN_CEDULA).exists():
            self.stdout.write(self.style.WARNING(f'Admin de desarrollo ya existe ({ADMIN_CEDULA}).'))
            return

        admin = Usuario.objects.create_superuser(
            username=ADMIN_CEDULA,
            email='admin.dev@medsync.local',
            password=ADMIN_PASSWORD,
            tipo_documento=tipo_documento,
            numero_documento=ADMIN_CEDULA,
            fecha_nacimiento='1990-01-01',
            first_name='Admin',
            last_name='Desarrollo',
        )
        grupo_admin, _ = Group.objects.get_or_create(name='Administrador')
        admin.groups.add(grupo_admin)
        self.stdout.write(self.style.SUCCESS(f'Admin de desarrollo creado: {ADMIN_CEDULA} / {ADMIN_PASSWORD}'))

    def _crear_paciente(self, tipo_documento):
        if Usuario.objects.filter(username=PACIENTE_CEDULA).exists():
            self.stdout.write(self.style.WARNING(f'Paciente de prueba ya existe ({PACIENTE_CEDULA}).'))
            return

        paciente = Usuario.objects.create_user(
            username=PACIENTE_CEDULA,
            email='paciente.dev@medsync.local',
            password=PACIENTE_PASSWORD,
            tipo_documento=tipo_documento,
            numero_documento=PACIENTE_CEDULA,
            fecha_nacimiento='1995-06-15',
            first_name='Paciente',
            last_name='Prueba',
        )
        grupo_paciente, _ = Group.objects.get_or_create(name='Paciente')
        paciente.groups.add(grupo_paciente)
        self.stdout.write(self.style.SUCCESS(f'Paciente de prueba creado: {PACIENTE_CEDULA} / {PACIENTE_PASSWORD}'))

    def _crear_medico(self, tipo_documento):
        if Usuario.objects.filter(username=MEDICO_CEDULA).exists():
            self.stdout.write(self.style.WARNING(f'Médico de prueba ya existe ({MEDICO_CEDULA}).'))
            return

        especialidad = Especialidad.objects.filter(activa=True).first()
        if especialidad is None:
            raise CommandError('No hay especialidades activas. Corre "migrate" primero.')

        form = RegistroMedicoForm(data={
            'nombre': 'Medico',
            'apellido': 'Prueba',
            'fecha_nacimiento': '1985-03-20',
            'tipo_documento': tipo_documento.pk,
            'numero_documento': MEDICO_CEDULA,
            'correo': 'medico.dev@medsync.local',
            'telefono': '',
            'especialidad': especialidad.pk,
            'registro_medico': 'RM-DEV-001',
            'password1': MEDICO_PASSWORD,
            'password2': MEDICO_PASSWORD,
        })
        if not form.is_valid():
            raise CommandError(f'No se pudo crear el médico de prueba: {form.errors.as_text()}')
        form.save()
        self.stdout.write(self.style.SUCCESS(f'Médico de prueba creado: {MEDICO_CEDULA} / {MEDICO_PASSWORD}'))
