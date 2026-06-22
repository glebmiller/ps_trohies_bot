import importlib.util
import sys
import types
from pathlib import Path


def _load_psn_bot_with_stubs():
    repo_root = Path(__file__).resolve().parents[2]
    module_name = "psn_bot_stats_test"

    database = types.ModuleType("database")
    database.getPSNToken = lambda: "test-npsso"
    database.getBotToken = lambda: "test-bot-token"
    database.get_chat_id = lambda: 1
    database.get_mongo_url = lambda: "mongodb://localhost:27017"

    psnawp_api = types.ModuleType("psnawp_api")
    psnawp_api.PSNAWP = lambda *args, **kwargs: types.SimpleNamespace()

    psnawp_search = types.ModuleType("psnawp_api.models.search")
    psnawp_search.SearchDomain = types.SimpleNamespace(FULL_GAMES="FULL_GAMES")

    psnawp_trophies = types.ModuleType("psnawp_api.models.trophies")
    psnawp_trophies.PlatformType = lambda value: value
    psnawp_trophies.TrophySet = type("TrophySet", (), {})

    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = lambda *args, **kwargs: types.SimpleNamespace(send_message=lambda *a, **k: None)

    class _Dispatcher:
        def __init__(self, *args, **kwargs):
            pass

        def message_handler(self, *args, **kwargs):
            return lambda func: func

        def callback_query_handler(self, *args, **kwargs):
            return lambda func: func

    aiogram.Dispatcher = _Dispatcher
    aiogram.executor = types.SimpleNamespace(start_polling=lambda *args, **kwargs: None)
    aiogram.types = types.SimpleNamespace()

    aiogram_types = types.ModuleType("aiogram.types")
    aiogram_types.InlineKeyboardMarkup = lambda *args, **kwargs: types.SimpleNamespace(add=lambda *a, **k: None)
    aiogram_types.InlineKeyboardButton = lambda *args, **kwargs: types.SimpleNamespace()

    pymongo = types.ModuleType("pymongo")
    pymongo.MongoClient = lambda *args, **kwargs: types.SimpleNamespace(
        PSNTrophies_new=types.SimpleNamespace(games=None, users=None)
    )

    pyrate_limiter = types.ModuleType("pyrate_limiter")
    pyrate_limiter.Duration = types.SimpleNamespace(SECOND=1)
    pyrate_limiter.Rate = lambda *args, **kwargs: types.SimpleNamespace()

    stubs = {
        "database": database,
        "psnawp_api": psnawp_api,
        "psnawp_api.models.search": psnawp_search,
        "psnawp_api.models.trophies": psnawp_trophies,
        "aiogram": aiogram,
        "aiogram.types": aiogram_types,
        "pymongo": pymongo,
        "pyrate_limiter": pyrate_limiter,
    }
    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(module_name, repo_root / "psn_bot.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_short_platinum_durations_are_not_rankable_fastest():
    psn_bot = _load_psn_bot_with_stubs()

    assert psn_bot._format_timedelta(220) == "3m"
    assert not psn_bot._is_rankable_platinum_seconds(220)
    assert psn_bot._is_rankable_platinum_seconds(3600)
