import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_name = os.getenv('DB_NAME')
conn = psycopg2.connect(
    dbname='postgres',
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
)
conn.autocommit = True
cur = conn.cursor()
cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
cur.execute(f'CREATE DATABASE "{db_name}"')
print(f'Base de datos {db_name} recreada.')