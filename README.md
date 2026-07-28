# MedSync

Sistema de agendación de citas médicas. Backend en Django 6, base de datos PostgreSQL.

## Requisitos

- Python 3.13+ (se desarrolló con 3.14)
- PostgreSQL corriendo localmente (por ejemplo con [DBngin](https://dbngin.com/))
- Git

## 1. Clonar el repositorio

```bash
git clone <url-del-repo>
cd proyectoPython
```

## 2. Crear y activar el entorno virtual

El `venv/` no viaja en el repo (está en `.gitignore`), cada quien crea el suyo.

**Windows (PowerShell / Git Bash):**
```bash
python -m venv venv
source venv/Scripts/activate   # Git Bash
# o: venv\Scripts\Activate.ps1  # PowerShell
```

**macOS / Linux:**
```bash
python -m venv venv
source venv/bin/activate
```

## 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

## 4. Variables de entorno

El archivo `.env` tampoco viaja en el repo. Créalo en la raíz del proyecto (junto a `manage.py`) con este contenido, ajustando usuario/contraseña a tu instalación local de PostgreSQL:

```env
SECRET_KEY=pon-aqui-una-clave-secreta-cualquiera-para-desarrollo
DB_NAME=agendamientoCitas
DB_USER=postgres
DB_PASSWORD=tu_password
DB_HOST=localhost
DB_PORT=5432
```

La base de datos (`DB_NAME`) debe existir antes de migrar. Créala desde tu cliente de PostgreSQL (pgAdmin, DBngin, `psql`, etc.) o con:

```bash
python -c "
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(dbname='postgres', user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST','localhost'), port=os.getenv('DB_PORT','5432'))
conn.autocommit = True
cur = conn.cursor()
cur.execute(f'CREATE DATABASE \"{os.getenv(\"DB_NAME\")}\"')
"
```

## 5. Migraciones

```bash
python manage.py migrate
```

Esto crea las tablas y, además, siembra automáticamente (vía migración de datos):
- Catálogo `TipoDocumento`: Cédula de ciudadanía, Tarjeta de identidad, Cédula de extranjería.
- Grupos de Django: `Administrador`, `Medico`, `Paciente`.

## 6. Crear un superusuario

```bash
python manage.py createsuperuser
```

Te pedirá, entre otros, dos campos que no son obvios en consola:

- **Tipo documento**: pide el **id** del `TipoDocumento`, no el nombre. Con la siembra por defecto, **`1` = Cédula de ciudadanía**, `2` = Tarjeta de identidad, `3` = Cédula de extranjería. Si tienes dudas, consulta los ids reales con:
  ```bash
  python manage.py shell -c "from usuarios.models import TipoDocumento; [print(t.pk, t.nombre) for t in TipoDocumento.objects.all()]"
  ```
- **Fecha de nacimiento**: formato `YYYY-MM-DD` (ej. `1990-05-10`).

## 7. Correr el servidor

```bash
python manage.py runserver
```

- App: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/
- Login: http://127.0.0.1:8000/cuentas/login/
- Registro: http://127.0.0.1:8000/registro/

El registro público de usuarios siempre crea el usuario en el grupo **Paciente** (server-side, no seleccionable desde el formulario). El `username` del usuario es su número de documento (cédula).

## 8. Correr los tests

```bash
python manage.py test
```

## Estructura relevante

```
config/            # settings, urls, wsgi/asgi
usuarios/          # modelo de usuario custom (AUTH_USER_MODEL), auth, registro
citas/             # (pendiente de implementar)
notificaciones/    # (pendiente de implementar)
templates/         # base.html + registration/{login,registro}.html
static/            # estáticos (imágenes, etc.)
docs/esquema_citas_v1.sql   # referencia de diseño de BD, NO ejecutar directo
```
