import os
import sys
import json
import logging
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

logging.basicConfig(level=logging.INFO)

# -------------------------------------------------------------------
# НАСТРОЙКИ
# -------------------------------------------------------------------
VK_TOKEN = os.getenv("VK_TOKEN") or os.getenv("BOT_TOKEN")
GROUP_ID = 123456789  # <- Вставишь сюда ID группы без кавычек

if not VK_TOKEN:
    print("❌ Ошибка: Не задан VK_TOKEN в переменных окружения!")
    sys.exit(1)

# -------------------------------------------------------------------
# ЗАПУСК БОТА
# -------------------------------------------------------------------
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

def start_bot():
    longpoll = VkBotLongPoll(vk_session, GROUP_ID)
    print("🚀 Бот для получения ID чата запущен и ждёт сообщений...")

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg = event.object.message
            peer_id = msg.get("peer_id")
            text = msg.get("text", "").strip().lower()

            # Работаем только с беседами (peer_id > 2000000000)
            if peer_id <= 2000000000:
                continue

            if text in ["/chat_id", "айди чата"]:
                vk.messages.send(
                    peer_id=peer_id,
                    message=f"🆔 ID этого чата (peer_id): {peer_id}",
                    random_id=get_random_id()
                )

if __name__ == "__main__":
    start_bot()