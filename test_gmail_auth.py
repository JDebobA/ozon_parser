# test_gmail_auth.py
import logging
import time
from gmail_auth import get_gmail_service, get_otp_code_from_email

logging.basicConfig(level=logging.INFO)

service = get_gmail_service("credentials.json", "token.json")

# Фиксируем момент "запроса кода" ПРЯМО СЕЙЧАС.
# После запуска скрипта у вас будет время вручную зайти на Ozon
# и запросить код на почту, пока скрипт ждёт.
start_time = time.time()
code = get_otp_code_from_email(service, timeout=120, after_timestamp=start_time)
print("Получен код:", code)