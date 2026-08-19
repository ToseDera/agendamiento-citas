from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from citas.models import DIAS_SEMANA, Especialidad, HorarioMedico

from .models import Medico, TipoDocumento, Usuario
from .validators import (
    validar_cambio_tipo_documento,
    validar_fecha_nacimiento_no_futura,
    validar_formato_numero_documento,
)

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
    confirmar_numero_documento = forms.CharField(
        max_length=25, label='Confirmar número de documento',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'placeholder': 'Repite tu número de documento'}),
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
            'numero_documento', 'confirmar_numero_documento', 'correo', 'telefono',
        )

    def clean_fecha_nacimiento(self):
        fecha_nacimiento = self.cleaned_data['fecha_nacimiento']
        error = validar_fecha_nacimiento_no_futura(fecha_nacimiento)
        if error:
            raise ValidationError(error)
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

            numero_normalizado, error = validar_formato_numero_documento(tipo_documento.codigo, numero_documento)
            if error:
                self.add_error('numero_documento', error)
            else:
                cleaned_data['numero_documento'] = numero_normalizado

        confirmar_numero_documento = cleaned_data.get('confirmar_numero_documento')
        if numero_documento and confirmar_numero_documento and numero_documento != confirmar_numero_documento:
            self.add_error(
                'confirmar_numero_documento', 'El número de documento no coincide con el ingresado arriba.',
            )

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La confirmación de cédula (tarea 5, fase 6a.1) es solo del registro
        # público: acá el número de documento lo escribe el Administrador
        # sobre la identidad de otra persona, no la persona autenticándose
        # sobre la suya propia.
        del self.fields['confirmar_numero_documento']

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


class DatosPersonalesForm(forms.ModelForm):
    """Formulario único de datos personales (fase 7, decisión 3): el
    conjunto de campos editables lo decide la vista, vía `campos_editables`
    — nunca esta clase, nunca la plantilla. Un campo ausente de
    `campos_editables` se borra de self.fields antes de bindear datos, así
    que un POST que lo incluya de todos modos no lo toca ni en la
    validación ni en save() (Django nunca lee un campo que no está
    declarado en el form)."""

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
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': DATE_INPUT_CLASS, 'type': 'date'}),
    )
    tipo_documento = forms.ModelChoiceField(
        queryset=TipoDocumento.objects.all(), label='Tipo de documento', empty_label=None,
        widget=SelectTipoDocumento(attrs={'class': SELECT_CLASS}),
    )

    class Meta:
        model = Usuario
        fields = ('nombre', 'apellido', 'correo', 'telefono', 'fecha_nacimiento', 'tipo_documento')

    def __init__(self, *args, campos_editables, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['nombre'].initial = self.instance.first_name
            self.fields['apellido'].initial = self.instance.last_name
            self.fields['correo'].initial = self.instance.email
        for nombre_campo in list(self.fields):
            if nombre_campo not in campos_editables:
                del self.fields[nombre_campo]

    def clean_correo(self):
        correo = self.cleaned_data['correo']
        existe = Usuario.objects.filter(email__iexact=correo).exclude(pk=self.instance.pk)
        if existe.exists():
            raise ValidationError('Ya existe una cuenta registrada con este correo electrónico.')
        return correo

    def clean_fecha_nacimiento(self):
        fecha_nacimiento = self.cleaned_data['fecha_nacimiento']
        error = validar_fecha_nacimiento_no_futura(fecha_nacimiento)
        if error:
            raise ValidationError(error)
        return fecha_nacimiento

    def clean(self):
        cleaned_data = super().clean()
        validar_cambio_tipo_documento(self)
        return cleaned_data

    def save(self, commit=True):
        usuario = super().save(commit=False)
        if 'nombre' in self.cleaned_data:
            usuario.first_name = self.cleaned_data['nombre']
        if 'apellido' in self.cleaned_data:
            usuario.last_name = self.cleaned_data['apellido']
        if 'correo' in self.cleaned_data:
            usuario.email = self.cleaned_data['correo']
        if commit:
            usuario.save()
        return usuario


class MedicoEditForm(forms.ModelForm):
    """Edición de datos de contacto, documento y especialidad de un médico
    existente (HU-12; tipo de documento agregado en fase 7, decisión 4).
    numero_documento nunca es un campo de este form — no se muestra en
    solo lectura vía un <input disabled> sino directamente como texto en la
    plantilla, así que ningún POST puede tocarlo bajo ningún nombre."""

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
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': DATE_INPUT_CLASS, 'type': 'date'}),
    )
    tipo_documento = forms.ModelChoiceField(
        queryset=TipoDocumento.objects.all(), label='Tipo de documento', empty_label=None,
        widget=SelectTipoDocumento(attrs={'class': SELECT_CLASS}),
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
        fields = ('nombre', 'apellido', 'correo', 'telefono', 'fecha_nacimiento', 'tipo_documento')

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

    def clean_fecha_nacimiento(self):
        fecha_nacimiento = self.cleaned_data['fecha_nacimiento']
        error = validar_fecha_nacimiento_no_futura(fecha_nacimiento)
        if error:
            raise ValidationError(error)
        return fecha_nacimiento

    def clean(self):
        cleaned_data = super().clean()
        validar_cambio_tipo_documento(self)
        return cleaned_data

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
        # medico no es un campo del form (construct_instance no lo llena):
        # tiene que estar en la instancia ANTES de _post_clean(), que es
        # quien llama a instance.full_clean() -> instance.clean() — única
        # vez que corre la validación de solapamiento (decisión 2, fase
        # 6b.2). Sin este atajo medico_id quedaría None y esa validación
        # se saltearía en silencio.
        self.instance.medico = self.medico
        return cleaned_data

    def save(self, commit=True):
        self.instance.medico = self.medico
        return super().save(commit=commit)
