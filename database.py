import os
from dotenv import load_dotenv

load_dotenv()


def getPSNToken():
    token = os.environ.get("NPSSO_CODE")
    if not token:
        raise RuntimeError("NPSSO_CODE environment variable is not set")
    return token


def getBotToken():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is not set")
    return token


def get_chat_id():
    chat_id = os.environ.get("CHAT_ID")
    if not chat_id:
        raise RuntimeError("CHAT_ID environment variable is not set")
    return int(chat_id)


def get_amazon_url():
    return os.environ.get("AMAZON_URL", "")


def get_mongo_url():
    return os.environ.get("MONGO_URL", "mongodb://localhost:27017")
