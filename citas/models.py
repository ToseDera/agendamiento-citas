from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Especialidad(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    descripcion = models.CharField(max_length=255, blank=True)
    duracion_cita_min = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(10), MaxValueValidator(120)],
    )
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Especialidad'
        verbose_name_plural = 'Especialidades'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


DIAS_SEMANA = [
    (0, 'Lunes'),
    (1, 'Martes'),
    (2, 'Miércoles'),
    (3, 'Jueves'),
    (4, 'Viernes'),
    (5, 'Sábado'),
    (6, 'Domingo'),
]


class HorarioMedico(models.Model):
    medico = models.ForeignKey(
        'usuarios.Medico', on_delete=models.CASCADE, related_name='horarios',
    )
    dia_semana = models.PositiveSmallIntegerField(choices=DIAS_SEMANA)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Horario de médico'
        verbose_name_plural = 'Horarios de médico'
        ordering = ['dia_semana', 'hora_inicio']
        constraints = [
            models.UniqueConstraint(
                fields=['medico', 'dia_semana', 'hora_inicio'],
                name='uq_horario_medico_dia_hora_inicio',
            ),
            models.CheckConstraint(
                condition=models.Q(hora_fin__gt=models.F('hora_inicio')),
                name='ck_horario_medico_fin_mayor_inicio',
            ),
        ]

    def __str__(self):
        return f'{self.medico} - {self.get_dia_semana_display()} {self.hora_inicio}-{self.hora_fin}'

    def clean(self):
        super().clean()
        if self.hora_inicio and self.hora_fin and self.hora_fin <= self.hora_inicio:
            raise ValidationError({'hora_fin': 'La hora de fin debe ser posterior a la hora de inicio.'})

        if self.medico_id and self.dia_semana is not None and self.hora_inicio and self.hora_fin:
            solapados = HorarioMedico.objects.filter(
                medico_id=self.medico_id, dia_semana=self.dia_semana,
                hora_inicio__lt=self.hora_fin, hora_fin__gt=self.hora_inicio,
            ).exclude(pk=self.pk)
            if solapados.exists():
                raise ValidationError(
                    'Este bloque se solapa con otro horario ya registrado para el médico ese día.',
                )
