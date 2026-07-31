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

El archivo `.env` tampoco viaja en el repo. Copia `.env.example` como `.env` en la raíz del proyecto (junto a `manage.py`) y ajusta usuario/contraseña a tu instalación local de PostgreSQL:

```bash
cp .env.example .env   # Windows (Git Bash) / macOS / Linux
```

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

### Opción rápida (recomendada para desarrollo): `seed_dev`

```bash
python manage.py seed_dev
```

Comando idempotente (correrlo varias veces no duplica nada) que solo funciona con `DEBUG=True`. Crea:

| Rol                                                                | Cédula (username) | Contraseña     |
| ------------------------------------------------------------------ | ------------------ | --------------- |
| Administrador (superusuario, grupo`Administrador`)               | `9999999999`     | `admin123`    |
| Paciente de prueba (grupo`Paciente`)                             | `8888888888`     | `paciente123` |
| Médico de prueba (grupo`Medico`, especialidad Medicina general) | `7777777777`     | `medico123`   |

Usa `python manage.py seed_dev --sin-extra` si solo quieres el admin, sin el paciente ni el médico de prueba.

### Opción manual: `createsuperuser`

```bash
python manage.py createsuperuser
```

Te pedirá, entre otros, dos campos que no son obvios en consola:

- **Tipo documento**: pide el **id** del `TipoDocumento`, no el nombre. Con la siembra por defecto, **`1` = Cédula de ciudadanía**, `2` = Tarjeta de identidad, `3` = Cédula de extranjería. Si tienes dudas, consulta los ids reales con:
  ```bash
  python manage.py shell -c "from usuarios.models import TipoDocumento; [print(t.pk, t.nombre) for t in TipoDocumento.objects.all()]"
  ```
- **Fecha de nacimiento**: formato `YYYY-MM-DD` (ej. `1990-05-10`).

Con `createsuperuser` el usuario queda como superusuario de Django, pero **no** entra automáticamente al grupo `Administrador` (que es lo que controla el acceso al panel `/panel/...` de la app) — únelo manualmente desde `/admin/` o por shell si necesitas ver el panel.

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

## Reiniciar el entorno desde cero

Cuando necesites dejar la base de datos limpia (por ejemplo tras un cambio grande de modelos), el flujo completo es:

```bash
# 1. Recrear la BD (ver el bloque de creación de BD en el paso 4; agrega un DROP DATABASE antes del CREATE)
python -c "
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
db_name = os.getenv('DB_NAME')
conn = psycopg2.connect(dbname='postgres', user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST','localhost'), port=os.getenv('DB_PORT','5432'))
conn.autocommit = True
cur = conn.cursor()
cur.execute(f'DROP DATABASE IF EXISTS \"{db_name}\" WITH (FORCE)')
cur.execute(f'CREATE DATABASE \"{db_name}\"')
"

# 2. Migrar (crea tablas + catálogos + grupos + especialidades iniciales)
python manage.py migrate

# 3. Sembrar datos de desarrollo (admin + paciente + médico de prueba)
python manage.py seed_dev

# 4. Levantar el servidor
python manage.py runserver
```

Después de estos 4 pasos el entorno queda listo para probar login, registro y el panel de gestión (`/panel/`) con las credenciales de la tabla del paso 6.

## Estructura relevante

```
config/            # settings, urls, wsgi/asgi
usuarios/          # modelo de usuario custom (AUTH_USER_MODEL), auth, registro, panel de médicos, seed_dev
citas/             # especialidades, médicos (horario), panel de especialidades
notificaciones/    # (pendiente de implementar)
templates/         # base.html + registration/{login,registro}.html + panel/
static/            # estáticos (imágenes, etc.)
docs/esquema_citas_v1.sql   # referencia de diseño de BD, NO ejecutar directo
```
