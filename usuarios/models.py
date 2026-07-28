from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class TipoDocumento(models.Model):
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        verbose_name = 'Tipo de documento'
        verbose_name_plural = 'Tipos de documento'

    def __str__(self):
        return self.nombre


class UsuarioManager(UserManager):
    def _create_user(self, username, email, password, **extra_fields):
        tipo_documento = extra_fields.get('tipo_documento')
        if tipo_documento is not None and not isinstance(tipo_documento, TipoDocumento):
            extra_fields['tipo_documento'] = TipoDocumento.objects.get(pk=tipo_documento)
        return super()._create_user(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    tipo_documento = models.ForeignKey(
        TipoDocumento, on_delete=models.PROTECT, null=False,
    )
    numero_documento = models.CharField(max_length=25)
    fecha_nacimiento = models.DateField()
    telefono = models.CharField(max_length=15, blank=True)
    email = models.EmailField(unique=True)

    REQUIRED_FIELDS = ['email', 'tipo_documento', 'fecha_nacimiento']

    objects = UsuarioManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tipo_documento', 'numero_documento'],
                name='uq_usuario_tipo_numero_documento',
            ),
        ]

    def __str__(self):
        return f'{self.get_full_name()} ({self.numero_documento})'
