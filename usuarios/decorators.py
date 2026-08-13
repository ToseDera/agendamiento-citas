from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def es_administrador(user):
    return user.is_authenticated and user.groups.filter(name='Administrador').exists()


def es_medico(user):
    return user.is_authenticated and user.groups.filter(name='Medico').exists()


def admin_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not es_administrador(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def medico_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not es_medico(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper
