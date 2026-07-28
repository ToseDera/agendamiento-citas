import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(dbname='postgres', user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'))
cur = conn.cursor()
cur.execute('SELECT datname FROM pg_database WHERE datistemplate = false')
print([r[0] for r in cur.fetchall()])