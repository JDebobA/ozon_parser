r"""
get_cookies.py

Подключается к уже запущенному вручную браузеру Chrome (через Chrome
DevTools Protocol) и забирает cookies авторизованной сессии data.ozon.ru.

Почему именно так:
1. Ozon использует антибот-защиту, которая блокирует автоматизированные
   браузеры (Playwright/Selenium), даже при полностью корректном
   прохождении формы логина (подтверждено экспериментально).
2. Начиная с Chrome 127+ (App-Bound Encryption), файл cookies на диске
   зашифрован так, что сторонние библиотеки (browser_cookie3 и т.п.)
   больше не могут его расшифровать напрямую.

Решение: логин выполняется человеком в обычном Chrome (проходит
антибот-защиту как реальный пользователь), а скрипт подключается
к этому же браузеру через Chrome DevTools Protocol и запрашивает
cookies через официальный API браузера — расшифровкой занимается
сам Chrome, а не наш код.

Перед запуском:
1. Полностью закройте все окна Chrome.
2. Запустите Chrome с флагом отладки, например (Windows):
   "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
       --remote-debugging-port=9222 ^
       --user-data-dir="C:\Users\<Имя>\AppData\Local\Google\Chrome\User Data"
3. В открывшемся окне зайдите на https://data.ozon.ru/ и залогиньтесь.
4. Запустите этот скрипт.
"""

import json
import logging
import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")


def get_cookies_via_cdp() -> list[dict]:
    """
    Подключается к уже запущенному Chrome через Chrome DevTools Protocol
    и возвращает cookies текущей сессии.
    """
    with sync_playwright() as p:
        logger.info("Подключаемся к запущенному Chrome по адресу %s", CDP_URL)
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            logger.error(
                "Не удалось подключиться к Chrome (%s). "
                "Убедитесь, что Chrome запущен с флагом --remote-debugging-port=9222",
                exc,
            )
            raise

        # Берём первый открытый контекст (окно/профиль) браузера
        context = browser.contexts[0]
        cookies = context.cookies()

        # Оставляем только cookies, относящиеся к ozon.ru,
        # чтобы не тащить в файл лишнее с других открытых вкладок
        ozon_cookies = [c for c in cookies if "ozon.ru" in c["domain"]]

        if not ozon_cookies:
            raise RuntimeError(
                "Cookies для ozon.ru не найдены среди открытых вкладок. "
                "Убедитесь, что вы залогинены на https://data.ozon.ru/ "
                "именно в этом окне Chrome."
            )

        logger.info("Найдено %d cookies для ozon.ru", len(ozon_cookies))
        browser.close()  # закрывает только подключение Playwright, не сам Chrome
        return ozon_cookies


def save_cookies(cookies: list[dict], path: str) -> None:
    """Сохраняет cookies в JSON-файл."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info("Cookies сохранены в %s", path)


def main():
    cookies = get_cookies_via_cdp()
    save_cookies(cookies, os.environ.get("COOKIES_PATH", "cookies.json"))


if __name__ == "__main__":
    main()