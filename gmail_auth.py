"""
Модуль для работы с Gmail API.

Часть 1: аутентификация.
При первом запуске откроет браузер для подтверждения доступа,
сохранит токен в token.json и будет переиспользовать его дальше
без повторного логина (пока токен не истечёт и не будет
отозван доступ).
"""

import logging
import os
import base64
import re
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Права доступа: нам достаточно только ЧТЕНИЯ почты,
# не даём скрипту прав на отправку/удаление писем поэтому приписываем readonly.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service(credentials_path: str, token_path: str):
    """
    Возвращает авторизованный объект для работы с Gmail API.

    :param credentials_path: путь к credentials.json (OAuth client, скачан из Google Cloud Console)
    :param token_path: путь к token.json (сохранённый токен доступа; создаётся автоматически)
    :return: объект googleapiclient service для вызова Gmail API
    """
    creds = None

    # Если токен уже был получен раньше — используем его
    if os.path.exists(token_path):
        logger.info("Найден сохранённый токен Gmail, пробуем использовать его")
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Если токена нет или он невалиден — получаем новый
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Токен Gmail истёк, обновляем через refresh_token")
            creds.refresh(Request())
        else:
            logger.info("Токен Gmail отсутствует, запускаем OAuth-флоу (откроется браузер)")
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Не найден файл {credentials_path}. "
                    "Скачайте OAuth client credentials из Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Сохраняем токен, чтобы не логиниться заново при следующих запусках
        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        logger.info("Токен Gmail сохранён в %s", token_path)

    service = build("gmail", "v1", credentials=creds)
    return service

# Отправитель писем с кодом подтверждения
OZON_SENDER = "mailer@sender.ozon.ru"

# Regex для извлечения 6-значного кода из текста письма
CODE_PATTERN = re.compile(r"\b(\d{6})\b")


def _get_message_text(service, message_id: str) -> str:
    """
    Достаёт текстовое содержимое письма по его ID.
    Gmail API отдаёт тело письма в base64url, плюс письмо может
    состоять из нескольких MIME-частей (text/plain, text/html) —
    обходим их рекурсивно и склеиваем весь текст.
    """
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    def extract_parts(payload) -> str:
        text_chunks = []
        body_data = payload.get("body", {}).get("data")
        if body_data:
            decoded = base64.urlsafe_b64decode(body_data.encode("UTF-8"))
            text_chunks.append(decoded.decode("utf-8", errors="ignore"))

        for part in payload.get("parts", []) or []:
            text_chunks.append(extract_parts(part))

        return "\n".join(text_chunks)

    return extract_parts(msg["payload"])

def get_otp_code_from_email(
    service,
    timeout: int = 90,
    poll_interval: int = 5,
    sender: str = OZON_SENDER,
    after_timestamp: float | None = None,
) -> str:
    """
    Ждёт СВЕЖЕЕ письмо от Ozon с кодом подтверждения и извлекает код.

    Чтобы не подхватить код из старого письма (например, от прошлой
    неудачной попытки логина), каждое найденное письмо проверяется
    по времени получения (internalDate) — учитываются только письма,
    пришедшие СТРОГО ПОСЛЕ after_timestamp.

    :param service: авторизованный Gmail service (из get_gmail_service)
    :param timeout: сколько всего секунд ждать письмо
    :param poll_interval: с какой периодичностью опрашивать почту
    :param sender: адрес отправителя, письма от которого ищем
    :param after_timestamp: unix-время (секунды), письма старше него игнорируются.
        Если не указано — берётся момент вызова функции (то есть учитываются
        только письма, которые придут уже ПОСЛЕ старта ожидания).
    :return: извлечённый код подтверждения (строка из цифр)
    :raises TimeoutError: если подходящее письмо не пришло за отведённое время
    """
    if after_timestamp is None:
        after_timestamp = time.time()

    logger.info(
        "Ожидаем письмо с кодом от %s, отправленное после %s (таймаут %s сек)",
        sender,
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(after_timestamp)),
        timeout,
    )

    deadline = time.monotonic() + timeout
    checked_ids = set()

    while time.monotonic() < deadline:
        # Гмайл-фильтр newer_than:1d — это грубая предфильтрация, чтобы не
        # перебирать всю переписку; точную проверку по времени делаем ниже.
        query = f"from:{sender} newer_than:1d"
        response = service.users().messages().list(
            userId="me", q=query, maxResults=10
        ).execute()

        messages = response.get("messages", [])

        # Письма от Gmail приходят в порядке "от новых к старым" —
        # это нам и нужно, чтобы в первую очередь проверять самое свежее.
        for message in messages:
            msg_id = message["id"]
            if msg_id in checked_ids:
                continue
            checked_ids.add(msg_id)

            # Достаём метаданные письма отдельным лёгким запросом,
            # чтобы проверить время получения ДО того, как тянуть полный текст.
            meta = service.users().messages().get(
                userId="me", id=msg_id, format="minimal"
            ).execute()
            internal_date_ms = int(meta["internalDate"])
            received_at = internal_date_ms / 1000

            if received_at <= after_timestamp:
                logger.debug(
                    "Письмо id=%s пришло раньше нужного момента, пропускаем", msg_id
                )
                continue

            text = _get_message_text(service, msg_id)
            match = CODE_PATTERN.search(text)
            if match:
                code = match.group(1)
                logger.info(
                    "Код подтверждения найден в свежем письме (id=%s, время=%s)",
                    msg_id,
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(received_at)),
                )
                return code
            else:
                logger.warning(
                    "Письмо id=%s подходит по времени и отправителю, но код в тексте не найден",
                    msg_id,
                )

        logger.info("Подходящее письмо ещё не пришло, повтор через %s сек", poll_interval)
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Не удалось получить код подтверждения от {sender} за {timeout} секунд "
        f"(письма старше {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(after_timestamp))} не учитывались)"
    )