import os
import psycopg2
from psycopg2 import pool

connection_pool = psycopg2.pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", 5432),
    dbname=os.getenv("DB_NAME", "vigilance_core"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "Aa@123456"),
)




if __name__ == "__main__":
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()

        cur.execute("SELECT 1;")

        print("Database connection successful!")
        print(cur.fetchone()[0])

        cur.close()
        connection_pool.putconn(conn)

    except Exception as e:
        print("Database connection failed!")
        print(e)