from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from citas.models import DIAS_SEMANA, Especialidad, HorarioMedico

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


class SelectTipoDocumento(forms.Select):
    """<select> de TipoDocumento que expone data-codigo en cada <option>,
    para que el JS de ayuda en el cliente pueda bifurcar por código (CC,
    TI, CE, PPT, PA) en vez de por el texto visible de la opción."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        tipo_documento = getattr(value, 'instance', None)
        if tipo_documento is not None and tipo_documento.codigo:
            option['attrs']['data-codigo'] = tipo_documento.codigo
        return option


class RegistroForm(UserCreationForm):
    nombre = forms.CharField(
        max_length=150, label='Nombres',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'given-name', 'placeholder': 'Ej. Juan'}),
    )
    apellido = forms.CharField(
        max_length=150, label='Apellidos',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'family-name', 'placeholder': 'Ej. Pérez'}),
    )
    correo = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'email', 'placeholder': 'ejemplo@correo.com'}),
    )
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': DATE_INPUT_CLASS, 'type': 'date'}),
    )
    tipo_documento = forms.ModelChoiceField(
        queryset=TipoDocumento.objects.all(), label='Tipo de documento',
        empty_label='Seleccione tipo de documento',
        widget=SelectTipoDocumento(attrs={'class': SELECT_CLASS}),
    )
    numero_documento = forms.CharField(
        max_length=25, label='Número de documento',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'placeholder': 'Ej. 1234567890'}),
    )
    telefono = forms.CharField(
        max_length=15, label='Teléfono', required=False,
        widget=forms.TextInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'tel', 'type': 'tel', 'placeholder': 'Ej. 3001234567'}),
    )
    password1 = forms.CharField(
        label='Contraseña', strip=False,
        widget=forms.PasswordInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'new-password', 'placeholder': '••••••••'}),
    )
    password2 = forms.CharField(
        label='Confirmar contraseña', strip=False,
        widget=forms.PasswordInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'new-password', 'placeholder': '••••••••'}),
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
        
        if tipo_documento and numero_documento:
            if Usuario.objects.filter(
                tipo_documento=tipo_documento, numero_documento=numero_documento,
            ).exists():
                self.add_error('numero_documento', 'Ya existe una cuenta con este número de documento.')
                
            codigo = tipo_documento.codigo
            length = len(numero_documento)

            if codigo == 'CC':
                if not numero_documento.isdigit():
                    self.add_error('numero_documento', 'La Cédula de Ciudadanía solo debe contener números.')
                elif not (5 <= length <= 10):
                    self.add_error('numero_documento', 'La Cédula de Ciudadanía debe tener entre 5 y 10 dígitos.')
            elif codigo == 'TI':
                if not numero_documento.isdigit():
                    self.add_error('numero_documento', 'La Tarjeta de Identidad solo debe contener números.')
                elif not (10 <= length <= 11):
                    self.add_error('numero_documento', 'La Tarjeta de Identidad debe tener entre 10 y 11 dígitos.')
            elif codigo == 'CE':
                if not numero_documento.isdigit():
                    self.add_error('numero_documento', 'La Cédula de Extranjería solo debe contener números.')
                elif not (6 <= length <= 8):
                    self.add_error('numero_documento', 'La Cédula de Extranjería debe tener entre 6 y 8 dígitos.')
            elif codigo == 'PPT':
                if not numero_documento.isdigit():
                    self.add_error('numero_documento', 'El Permiso Especial solo debe contener números.')
                elif not (6 <= length <= 8):
                    self.add_error('numero_documento', 'El Permiso Especial debe tener entre 6 y 8 dígitos.')
            elif codigo == 'PA':
                if not numero_documento.isalnum():
                    self.add_error('numero_documento', 'El Pasaporte solo debe contener letras y números.')
                elif not (6 <= length <= 16):
                    self.add_error('numero_documento', 'El Pasaporte debe tener entre 6 y 16 caracteres.')
                else:
                    cleaned_data['numero_documento'] = numero_documento.upper()
            else:
                if not numero_documento.isdigit():
                    self.add_error('numero_documento', 'El número de documento solo debe contener números.')
                elif not (5 <= length <= 16):
                    self.add_error('numero_documento', 'El número de documento debe tener entre 5 y 16 dígitos.')

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
        queryset=Especialidad.objects.filter(activa=True), label='Especialidad',
        empty_label='Seleccione especialidad',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )
    registro_medico = forms.CharField(
        max_length=30, required=False, label='Registro profesional',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'placeholder': 'Ej. 12345-RM'}),
    )

    class Meta(RegistroForm.Meta):
        fields = RegistroForm.Meta.fields + ('especialidad', 'registro_medico')

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
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'given-name', 'placeholder': 'Ej. Juan'}),
    )
    apellido = forms.CharField(
        max_length=150, label='Apellidos',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'autocomplete': 'family-name', 'placeholder': 'Ej. Pérez'}),
    )
    correo = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'email', 'placeholder': 'ejemplo@correo.com'}),
    )
    telefono = forms.CharField(
        max_length=15, label='Teléfono', required=False,
        widget=forms.TextInput(attrs={'class': ICON_INPUT_CLASS, 'autocomplete': 'tel', 'type': 'tel', 'placeholder': 'Ej. 3001234567'}),
    )
    especialidad = forms.ModelChoiceField(
        queryset=None, label='Especialidad',
        empty_label='Seleccione especialidad',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )
    registro_medico = forms.CharField(
        max_length=30, required=False, label='Registro profesional',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'placeholder': 'Ej. 12345-RM'}),
    )

    class Meta:
        model = Usuario
        fields = ('nombre', 'apellido', 'correo', 'telefono')

    def __init__(self, *args, medico=None, **kwargs):
        self.medico = medico
        super().__init__(*args, **kwargs)

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
    dia_semana = forms.TypedChoiceField(
        choices=[('', 'Seleccione día')] + list(DIAS_SEMANA),
        coerce=int, label='Día',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )

    def __init__(self, *args, medico=None, **kwargs):
        self.medico = medico
        super().__init__(*args, **kwargs)

    class Meta:
        model = HorarioMedico
        fields = ('dia_semana', 'hora_inicio', 'hora_fin')
        widgets = {
            'hora_inicio': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
            'hora_fin': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
        }
        labels = {'hora_inicio': 'Hora de inicio', 'hora_fin': 'Hora de fin'}

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
