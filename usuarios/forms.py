from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from .models import TipoDocumento, Usuario


class RegistroForm(UserCreationForm):
    nombre = forms.CharField(max_length=150, label='Nombres')
    apellido = forms.CharField(max_length=150, label='Apellidos')
    correo = forms.EmailField(label='Correo electrónico')
    fecha_nacimiento = forms.DateField(label='Fecha de nacimiento')
    tipo_documento = forms.ModelChoiceField(
        queryset=TipoDocumento.objects.all(), label='Tipo de documento',
        widget=forms.Select(attrs={
            'class': (
                'appearance-none block w-full px-3 py-2 border border-outline-variant '
                'rounded-lg shadow-sm focus:outline-none focus:ring-primary '
                'focus:border-primary font-body-md text-body-md bg-surface-bright '
                'text-on-surface'
            ),
        }),
    )
    numero_documento = forms.CharField(max_length=25, label='Número de documento')
    telefono = forms.CharField(max_length=15, label='Teléfono', required=False)

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
