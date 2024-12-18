import schedule
import time
from database import update_premium_days

def my_function():
    print("Функция запущена!")

# Планируем выполнение функции каждый день в 10:30
schedule.every().day.at("1:00").do(update_premium_days)

while True:
    schedule.run_pending()  # Проверяем, есть ли запланированные задачи
    time.sleep(60)  # Ждем 60 секунд перед следующим циклом