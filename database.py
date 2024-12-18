import psycopg2
from psycopg2 import sql, OperationalError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import time
from datetime import datetime
import asyncio

scheduler = AsyncIOScheduler()
def create_connection():
    """Создает соединение с базой данных PostgreSQL."""
    try:
        conn = psycopg2.connect(
            dbname="telegram_db",
            user="postgres",
            password="postgres",
            host="localhost",
            port="5432"
        )
        return conn
    except OperationalError as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None


def add_newuser_db(telegram_id):
    """Добавляет нового пользователя в базу данных."""
    conn = create_connection()
    if conn is not None:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (telegram_id) VALUES (%s) ON CONFLICT (telegram_id) DO NOTHING;",
                (telegram_id,)
            )
            conn.commit()
        except Exception as e:
            print(f"Ошибка добавления пользователя: {e}")
        finally:
            cur.close()
            conn.close()

def update_db(telegram_id,  paid_requests, prem_days):
    """Обновляет количество запросов пользователя."""
    conn = create_connection()
    if conn is not None:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE users 
                SET paid_requests = paid_requests + %s,
                    premium_days_remaining = premium_days_remaining + %s
                WHERE telegram_id = %s;
                """,
                (paid_requests, prem_days, telegram_id)
            )
            conn.commit()
            print(f"Запросы для пользователя с Telegram ID {telegram_id} обновлены.")
        except Exception as e:
            print(f"Ошибка обновления запросов: {e}")
        finally:
            cur.close()
            conn.close()

def get_user_info_db(telegram_id):
    """Получает информацию о пользователе."""
    conn = create_connection()
    if conn is not None:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s;",
                (telegram_id,)
            )
            user_info = cur.fetchone()
            if user_info:
                return user_info
            else:
                return "Не найден"
        except Exception as e:
            print(f"Ошибка получения информации о пользователе: {e}")
        finally:
            cur.close()
            conn.close()


def prom(telegram_id):
    try:
        conn = create_connection()
        if conn is not None:
            cur = conn.cursor()

        # SQL-запрос для обновления значений
        update_query = """
                WITH updated AS (
                    UPDATE users
                    SET free_requests_today = CASE 
                        WHEN free_requests_today > 0 THEN free_requests_today - 1
                        ELSE free_requests_today
                    END,
                    paid_requests = CASE 
                        WHEN free_requests_today = 0 AND paid_requests > 0 THEN paid_requests - 1
                        ELSE paid_requests
                    END
                    WHERE telegram_id = %s
                    RETURNING free_requests_today, paid_requests
                )
                SELECT * FROM updated;
                """

        cur.execute(update_query, (telegram_id,))
        result = cur.fetchone()

        if result is None:
            raise Exception("Пользователь не найден.")

        free_requests_today, paid_requests = result

        # Проверка на нулевые значения
        if free_requests_today < 0 and paid_requests < 0:
            raise Exception("Ошибка: у вас закончились бесплатные и платные запросы.")

        # Фиксация изменений
        conn.commit()

    except Exception as e:
        print(f"Ошибка: {e}")
    finally:        #Закрытие курсора и соединения
        if cur:
            cur.close()
        if conn:
            conn.close()

def update_premium_days():
    conn = create_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET premium_days_remaining = GREATEST(premium_days_remaining - 1, 0)
                WHERE premium_days_remaining > 0;
            """)
            conn.commit()
            print("Premium days updated successfully.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()


