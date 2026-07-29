from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Medico, TipoDocumento, Usuario

INPUT_CLASS = (
    'appearance-none block w-full px-3 py-2 border border-outline-variant '
    'rounded-lg shadow-sm placeholder-outline focus:outline-none '
    'focus:ring-primary focus:border-primary font-body-md text-body-md '
    'bg-surface-bright'
)
DATE_INPUT_CLASS = (
    'appearance-none block w-full px-3 py-2 border border-outline-variant '
    'rounded-lg shadow-sm focus:outline-none focus:ring-primary '
    'focus:border-primary font-body-md text-body-md bg-surface-bright '
    'text-on-surface'
)
ICON_INPUT_CLASS = (
    'appearance-none block w-full pl-10 px-3 py-2 border border-outline-variant '
    'rounded-lg shadow-sm placeholder-outline focus:outline-none '
    'focus:ring-primary focus:border-primary font-body-md text-body-md '
    'bg-surface-bright'
)
SELECT_CLASS = (
    'appearance-none block w-full px-3 py-2 border border-outline-variant '
    'rounded-lg shadow-sm focus:outline-none focus:ring-primary '
    'focus:border-primary font-body-md text-body-md bg-surface-bright '
    'text-on-surface'
)


class RegistroForm(UserCreationForm):
    nombre = forms.CharField(
        max_length=150, label='Nombres',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'given-name'}),
    )
    apellido = forms.CharField(
        max_length=150, label='Apellidos',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'family-name'}),
    )
    correo = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'email'}),
    )
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': DATE_INPUT_CLASS, 'type': 'date'}),
    )
    tipo_documento = forms.ModelChoiceField(
        queryset=TipoDocumento.objects.all(), label='Tipo de documento',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )
    numero_documento = forms.CharField(
        max_length=25, label='Número de documento',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )
    telefono = forms.CharField(
        max_length=15, label='Teléfono', required=False,
        widget=forms.TextInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'tel', 'type': 'tel'}),
    )
    password1 = forms.CharField(
        label='Contraseña', strip=False,
        widget=forms.PasswordInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirmar contraseña', strip=False,
        widget=forms.PasswordInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'new-password'}),
    )

    class Meta:
        model = Usuario
        fields = (
            'nombre', 'apellido', 'fecha_nacimiento', 'tipo_documento',
            'numero_documento', 'correo', 'telefono',
        )

    def clean_fecha_nacimiento(self):
        fecha_nacimiento = self.cleaned_data['fecha_nacimiento']
        if fecha_nacimiento > date.today():
            raise ValidationError('La fecha de nacimiento no puede ser una fecha futura.')
        return fecha_nacimiento

    def clean_numero_documento(self):
        numero_documento = self.cleaned_data['numero_documento']
        if not numero_documento.isdigit():
            raise ValidationError('El número de documento solo debe contener dígitos.')
        if not (6 <= len(numero_documento) <= 15):
            raise ValidationError('El número de documento debe tener entre 6 y 15 dígitos.')
        if Usuario.objects.filter(username=numero_documento).exists():
            raise ValidationError('Ya existe una cuenta con este número de documento.')
        return numero_documento

    def clean_correo(self):
        correo = self.cleaned_data['correo']
        if Usuario.objects.filter(email__iexact=correo).exists():
            raise ValidationError('Ya existe una cuenta registrada con este correo electrónico.')
        return correo

    def clean(self):
        cleaned_data = super().clean()
        tipo_documento = cleaned_data.get('tipo_documento')
        numero_documento = cleaned_data.get('numero_documento')
        if tipo_documento and numero_documento and Usuario.objects.filter(
            tipo_documento=tipo_documento, numero_documento=numero_documento,
        ).exists():
            self.add_error('numero_documento', 'Ya existe una cuenta con este número de documento.')
        return cleaned_data

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.first_name = self.cleaned_data['nombre']
        usuario.last_name = self.cleaned_data['apellido']
        usuario.email = self.cleaned_data['correo']
        usuario.fecha_nacimiento = self.cleaned_data['fecha_nacimiento']
        usuario.tipo_documento = self.cleaned_data['tipo_documento']
        usuario.numero_documento = self.cleaned_data['numero_documento']
        usuario.telefono = self.cleaned_data.get('telefono', '')
        usuario.username = self.cleaned_data['numero_documento']
        if commit:
            usuario.save()
            grupo_paciente, _ = Group.objects.get_or_create(name='Paciente')
            usuario.groups.add(grupo_paciente)
        return usuario


class RegistroMedicoForm(RegistroForm):
    """Registro de médico por el Administrador: crea Usuario + Medico en una transacción."""

    especialidad = forms.ModelChoiceField(
        queryset=None, label='Especialidad',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )
    registro_medico = forms.CharField(
        max_length=30, required=False, label='Registro profesional',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )

    class Meta(RegistroForm.Meta):
        fields = RegistroForm.Meta.fields + ('especialidad', 'registro_medico')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from citas.models import Especialidad
        self.fields['especialidad'].queryset = Especialidad.objects.filter(activa=True)

    def save(self, commit=True):
        usuario = super().save(commit=False)
        if commit:
            with transaction.atomic():
                usuario.save()
                grupo_medico, _ = Group.objects.get_or_create(name='Medico')
                usuario.groups.add(grupo_medico)
                Medico.objects.create(
                    usuario=usuario,
                    especialidad=self.cleaned_data['especialidad'],
                    registro_medico=self.cleaned_data.get('registro_medico', ''),
                )
        return usuario


class MedicoEditForm(forms.ModelForm):
    """Edición de datos de contacto y especialidad de un médico existente (HU-12)."""

    nombre = forms.CharField(
        max_length=150, label='Nombres',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'given-name'}),
    )
    apellido = forms.CharField(
        max_length=150, label='Apellidos',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'family-name'}),
    )
    correo = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'email'}),
    )
    telefono = forms.CharField(
        max_length=15, label='Teléfono', required=False,
        widget=forms.TextInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'tel', 'type': 'tel'}),
    )
    especialidad = forms.ModelChoiceField(
        queryset=None, label='Especialidad',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )
    registro_medico = forms.CharField(
        max_length=30, required=False, label='Registro profesional',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )

    class Meta:
        model = Usuario
        fields = ('nombre', 'apellido', 'correo', 'telefono')

    def __init__(self, *args, medico=None, **kwargs):
        self.medico = medico
        super().__init__(*args, **kwargs)
        from citas.models import Especialidad

        if self.instance.pk:
            self.fields['nombre'].initial = self.instance.first_name
            self.fields['apellido'].initial = self.instance.last_name
            self.fields['correo'].initial = self.instance.email
            self.fields['telefono'].initial = self.instance.telefono

        especialidades = Especialidad.objects.filter(activa=True)
        if medico and medico.especialidad_id and not medico.especialidad.activa:
            especialidades = especialidades | Especialidad.objects.filter(pk=medico.especialidad_id)
        self.fields['especialidad'].queryset = especialidades.distinct()

        if medico:
            self.fields['especialidad'].initial = medico.especialidad_id
            self.fields['registro_medico'].initial = medico.registro_medico

    def clean_correo(self):
        correo = self.cleaned_data['correo']
        existe = Usuario.objects.filter(email__iexact=correo).exclude(pk=self.instance.pk)
        if existe.exists():
            raise ValidationError('Ya existe una cuenta registrada con este correo electrónico.')
        return correo

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.first_name = self.cleaned_data['nombre']
        usuario.last_name = self.cleaned_data['apellido']
        usuario.email = self.cleaned_data['correo']
        usuario.telefono = self.cleaned_data.get('telefono', '')
        if commit:
            usuario.save()
            self.medico.especialidad = self.cleaned_data['especialidad']
            self.medico.registro_medico = self.cleaned_data.get('registro_medico', '')
            self.medico.save()
        return usuario


def _copiar_errores_de_validacion(form, error):
    if hasattr(error, 'error_dict'):
        for field, field_errors in error.message_dict.items():
            target = field if field in form.fields else None
            for message in field_errors:
                form.add_error(target, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


class HorarioMedicoForm(forms.ModelForm):
    def __init__(self, *args, medico=None, **kwargs):
        self.medico = medico
        super().__init__(*args, **kwargs)

    class Meta:
        from citas.models import HorarioMedico
        model = HorarioMedico
        fields = ('dia_semana', 'hora_inicio', 'hora_fin')
        widgets = {
            'dia_semana': forms.Select(attrs={'class': SELECT_CLASS}),
            'hora_inicio': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
            'hora_fin': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
        }
        labels = {'dia_semana': 'Día', 'hora_inicio': 'Hora de inicio', 'hora_fin': 'Hora de fin'}

    def clean(self):
        cleaned_data = super().clean()
        self.instance.medico = self.medico
        self.instance.dia_semana = cleaned_data.get('dia_semana')
        self.instance.hora_inicio = cleaned_data.get('hora_inicio')
        self.instance.hora_fin = cleaned_data.get('hora_fin')
        try:
            self.instance.clean()
        except ValidationError as error:
            _copiar_errores_de_validacion(self, error)
        return cleaned_data

    def save(self, commit=True):
        self.instance.medico = self.medico
        return super().save(commit=commit)
