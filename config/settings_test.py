"""
Settings SOLO para `manage.py test --settings=config.settings_test`.

No lo importa nada de la app en tiempo de ejecución normal (runserver, wsgi,
management commands que no sean tests) — manage.py sigue apuntando a
config.settings por defecto (ver DJANGO_SETTINGS_MODULE en manage.py), así
que este archivo no cambia nada fuera de una corrida de tests explícita.

MD5PasswordHasher es criptográficamente débil a propósito: es la opción que
la propia documentación de Django recomienda para acelerar tests, nunca para
producción. Por eso vive en un módulo de settings separado en vez de una
rama condicional dentro de config/settings.py — así no hay ningún camino de
ejecución en el que este hasher pueda quedar activo fuera de un test.
"""
from .settings import *  # noqa: F401,F403

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]
