from psnawp_api import PSNAWP
from psnawp_api.models.search import SearchDomain
from psnawp_api.models.trophies import PlatformType, TrophySet
from time import sleep
import json
import hashlib
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, executor, types
import pymongo
import re
import database
import os
import asyncio
import random
import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import csv
from typing import List
from itertools import groupby, chain
from statistics import mean


def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def search_title_id(title_name: str) -> str | None:
    """Search PSN for a game by name and return its title_id (e.g. CUSA00419_00).

    Uses the v3 GraphQL-based search API (the old universal search was deprecated by Sony).
    """
    try:
        for result in psnawp.search(search_query=title_name, search_domain=SearchDomain.FULL_GAMES, limit=3):
            if result is None:
                continue
            # The product ID with CUSA/PPSA is in the nested result field
            inner = result.get("result") or {}
            candidate_ids = []
            if inner.get("__typename") == "Product":
                candidate_ids.append(inner.get("id", ""))
            elif inner.get("__typename") == "Concept":
                default_product = inner.get("defaultProduct") or {}
                candidate_ids.append(default_product.get("id", ""))
            # Also check the top-level id as fallback
            candidate_ids.append(result.get("id", ""))
            for candidate in candidate_ids:
                match = re.search(r"((?:CUSA|PPSA|NPWR)\d+_\d+)", candidate)
                if match:
                    return match.group(1)
    except Exception as e:
        logging.warning("search_title_id error: %s", e)
    return None


def get_user_presence(user) -> str | None:
    """Get what game a user is currently playing, if any."""
    try:
        presence = user.get_presence()
        primary = presence.get("basicPresence", {})
        availability = primary.get("availability", "")
        if availability == "availableToPlay":
            game_info = primary.get("gameTitleInfoList", [])
            if game_info:
                return game_info[0].get("titleName")
    except Exception as e:
        logging.debug("Could not get presence for %s: %s", user.online_id, e)
    return None


from pyrate_limiter import Duration, Rate

# Configure logging
logging.basicConfig(level=logging.INFO)
psnawp = PSNAWP(database.getPSNToken(), rate_limit=Rate(1, Duration.SECOND * 1))

# connect to db
mongo_client = pymongo.MongoClient(database.get_mongo_url())
db = mongo_client.PSNTrophies_new

# general table without trophy names
all_stats = db.games
# users' table
users_collection = db.users

# temporary store for callback data that exceeds Telegram's 64-byte limit
_callback_store = {}
_cache_lock = asyncio.Lock()
_psn_job_lock = asyncio.Lock()
DAILY_CACHE_HOUR_UTC = 4
AUTOPOP_PLATINUM_MAX_SECONDS = 60 * 60
FASTEST_PLATINUM_MIN_SECONDS = 60 * 60


def _store_callback_data(data: str) -> str:
    """If data fits in 64 bytes, return as-is. Otherwise store and return a short key."""
    if len(data.encode("utf-8")) <= 64:
        return data
    key = "cb:" + hashlib.sha256(data.encode()).hexdigest()[:12]
    _callback_store[key] = data
    return key


def _resolve_callback_data(data: str) -> str:
    """Resolve callback data — look up from store if it was shortened."""
    if data.startswith("cb:"):
        return _callback_store.get(data, data)
    return data


def _clean_username_arg(value: str) -> str:
    return value.strip().strip("'\"")


def _resolve_tracked_login(login: str) -> str | None:
    """Return the stored PSN username, matching case-insensitively if needed."""
    login = _clean_username_arg(login)
    if not login:
        return None
    user_doc = users_collection.find_one({"_id": login}, {"_id": 1})
    if user_doc:
        return user_doc["_id"]
    user_doc = users_collection.find_one({"_id": re.compile(f"^{re.escape(login)}$", re.IGNORECASE)}, {"_id": 1})
    return user_doc["_id"] if user_doc else None


def _link_telegram_user(login: str, message) -> None:
    """Remember which Telegram user added/claimed a tracked PSN username."""
    if not message.from_user:
        return
    update = {
        "telegram_user_id": message.from_user.id,
        "telegram_chat_id": message.chat.id,
    }
    if message.from_user.username:
        update["telegram_username"] = message.from_user.username
    users_collection.update_one({"_id": login}, {"$set": update})


def _resolve_me_login(message) -> str | None:
    if message.from_user:
        user_doc = users_collection.find_one({"telegram_user_id": message.from_user.id}, {"_id": 1})
        if user_doc:
            return user_doc["_id"]
        if message.from_user.username:
            return _resolve_tracked_login(message.from_user.username)
    return None


def _platform_name(platform) -> str:
    return str(platform or "").upper().replace(" ", "")


def _normalize_title_for_crossgen(title: str | None) -> str:
    value = (title or "").lower()
    value = re.sub(r"\b(?:ps4|ps5|playstation\s*[45])\b", " ", value)
    value = re.sub(r"\b(?:remastered|director'?s cut|definitive|complete|ultimate|standard|deluxe|goty)\s+edition\b", " ", value)
    value = re.sub(r"\b(?:remastered|director'?s cut)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _games_by_normalized_title(games):
    games_by_title = {}
    for game in games:
        normalized_title = _normalize_title_for_crossgen(game.get("title"))
        if normalized_title:
            games_by_title.setdefault(normalized_title, []).append(game)
    return games_by_title


def _load_crossgen_context(games):
    context = {game.get("_id"): game for game in games if game is not None}
    normalized_titles = {_normalize_title_for_crossgen(game.get("title")) for game in games if game is not None}
    normalized_titles.discard("")
    for game in games:
        title = game.get("title")
        if not title:
            continue
        for candidate in all_stats.find({"title": title}):
            context[candidate.get("_id")] = candidate
    if normalized_titles:
        for candidate in all_stats.find({}, {"_id": 1, "title": 1, "title platform": 1, "platinum_cache": 1}):
            if _normalize_title_for_crossgen(candidate.get("title")) in normalized_titles:
                context[candidate.get("_id")] = candidate
    return list(context.values())


def _is_crossgen_autopop_platinum(game, login, games_by_normalized_title) -> bool:
    if _platform_name(game.get("title platform")) != "PS5":
        return False

    platinum = game.get("platinum_cache", {}).get(login)
    if not platinum:
        return False

    seconds = platinum.get("time_delta_seconds")
    if seconds is None or seconds > AUTOPOP_PLATINUM_MAX_SECONDS:
        return False

    normalized_title = _normalize_title_for_crossgen(game.get("title"))
    for candidate in games_by_normalized_title.get(normalized_title, []):
        if candidate.get("_id") == game.get("_id"):
            continue
        if _platform_name(candidate.get("title platform")) == "PS4" and candidate.get("platinum_cache", {}).get(login):
            return True
    return False


def _platinum_duration(row):
    if len(row) > 3 and isinstance(row[3], timedelta):
        return row[3]
    return None


def _platinum_sort_seconds(row):
    duration = _platinum_duration(row)
    return duration.total_seconds() if duration is not None else float("inf")


def _is_rankable_platinum_seconds(seconds) -> bool:
    return seconds is not None and seconds >= FASTEST_PLATINUM_MIN_SECONDS


def _is_rankable_platinum_row(row) -> bool:
    duration = _platinum_duration(row)
    return duration is not None and duration.total_seconds() >= FASTEST_PLATINUM_MIN_SECONDS and not _is_autopop_row(row)


def _mark_autopop_row(row, is_autopop):
    while len(row) < 4:
        row.append(None)
    if len(row) == 4:
        row.append(is_autopop)
    else:
        row[4] = is_autopop


def _is_autopop_row(row) -> bool:
    return len(row) > 4 and row[4] is True


# таблица игр со списком трофеев с названиями
# games_collection = db.games_with_trophies

CHATID = database.get_chat_id()

# Setting up telegram bot Wisely
TOKEN = database.getBotToken()
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)


TROPHY_EMOJI = {
    "PLATINUM": "🏆",
    "GOLD": "🥇",
    "SILVER": "🥈",
    "BRONZE": "🥉",
}


async def send_trophy(user_name, game_name, platform, trophy, platinum=False):
    """Send a single trophy notification as a Telegram photo + caption."""
    emoji = TROPHY_EMOJI.get(trophy["trophytype"], "")
    caption = (
        f"{emoji} <b>{trophy['trophy_name']}</b>\n"
        f"{trophy['description']}\n"
        f"{trophy['percentage']}% — {trophy['trophytype']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{user_name} — {game_name} — {platform.upper()}"
    )
    try:
        await bot.send_photo(CHATID, trophy["icon"], caption=caption, parse_mode=types.ParseMode.HTML)
    except Exception as e:
        logging.warning("Failed to send photo, sending text: %s", e)
        await bot.send_message(CHATID, caption, parse_mode=types.ParseMode.HTML)
    if platinum:
        await bot.send_sticker(CHATID, sticker="CAACAgIAAxkBAAEV9xliz9cW_7inof3UGYHVLF3AbJuy_QACTwsAAkKvaQABE3jwX_D6RZYpBA")


async def notify_trophies(trophies):
    """Send trophy notifications for a list of earned trophies."""
    logging.info("Processing trophies: %s", trophies)
    user_name, game_name, platform = trophies[0], trophies[1], trophies[2]

    for trophy in trophies[3:]:
        users_collection.update_one({"_id": user_name}, {"$set": {"date_added": datetime.now(timezone.utc)}})
        plat = trophy["trophytype"] == "PLATINUM"
        await send_trophy(user_name, game_name, platform, trophy, platinum=plat)
        await asyncio.sleep(5)


def _poll_friend_trophies():
    """Blocking function that polls PSN friends for new trophies.

    Returns a list of (received_trophies_list, ...) batches to notify about.
    Runs in a thread so it doesn't block the event loop.
    """
    psn_client = psnawp.me()
    bot_friends = psn_client.friends_list()
    notifications = []

    for friend in bot_friends:
        logging.info("Checking friend: %s", friend.online_id)
        user_doc = users_collection.find_one({"_id": friend.online_id})
        if user_doc is None:
            logging.info("Friend %s not in DB, skipping", friend.online_id)
            continue
        last_trophy_date = user_doc["date_added"]

        # get new trophies since last trophy recorded in DB
        psn_user = psnawp.user(online_id=friend.online_id)
        for trophy_title in psn_user.trophy_titles(limit=3):
            logging.info("Trophy title: %s", trophy_title.title_name)

            # Always update progress in DB (catches DLC percentage changes)
            np_comm_id = trophy_title.np_communication_id
            current_progress = trophy_title.progress
            existing_game = all_stats.find_one({"_id": np_comm_id})
            if existing_game is not None:
                update_game_in_collection(np_comm_id, friend.online_id, current_progress)

            last_updated_date_time = trophy_title.last_updated_datetime
            # compare last trophy date in DB with last trophy date in PSN
            if last_updated_date_time is None:
                continue
            # fix can't compare offset-naive and offset-aware datetimes error
            last_updated_date_time = last_updated_date_time.replace(tzinfo=None)

            if last_trophy_date < last_updated_date_time:
                logging.info("new trophy")
                # get game name
                game_name = trophy_title.title_name
                # get game id
                np_communication_id = trophy_title.np_communication_id
                # get platform
                platform = next(iter(trophy_title.title_platform)).value
                # get progress
                progress = trophy_title.progress
                # get icon url
                icon_url = trophy_title.title_icon_url
                # if game not in DB
                if all_stats.find_one({"_id": np_communication_id}) is None:
                    add_game_to_collection(np_communication_id, game_name, friend.online_id, progress, platform, icon_url)
                    logging.info(f"{game_name} added to DB")
                else:
                    update_game_in_collection(np_communication_id, friend.online_id, progress)
                    logging.info(f"{game_name} updated in DB")

                game_trophies = list(
                    psn_user.trophies(np_communication_id=np_communication_id, platform=PlatformType(platform), include_progress=True, trophy_group_id="all")
                )

                received_trophies = [friend.online_id, game_name, platform]

                for single_trophy in game_trophies:
                    if single_trophy.earned_date_time is None:
                        continue

                    new_trophy_date = single_trophy.earned_date_time.replace(tzinfo=None)
                    last_trophy_date = last_trophy_date.replace(tzinfo=None)

                    if new_trophy_date > last_trophy_date:
                        received_trophy = {}
                        received_trophy["trophy_id"] = single_trophy.trophy_id
                        received_trophy["percentage"] = single_trophy.trophy_earn_rate
                        received_trophy["trophytype"] = single_trophy.trophy_type.name
                        received_trophy["icon"] = single_trophy.trophy_icon_url
                        received_trophy["description"] = single_trophy.trophy_detail
                        received_trophy["trophy_name"] = single_trophy.trophy_name
                        received_trophies.append(received_trophy)

                # get title_id the same way like it's done in function check_platinum
                title_ids = get_title_ids_by_name(game_name)
                title_id = None
                for try_title_id in title_ids:
                    try:
                        for trophy_title_info in psn_user.trophy_titles_for_title(title_ids=[try_title_id]):
                            logging.info("id found")
                            title_id = try_title_id
                            break
                    except Exception:
                        logging.info("No trophies for this game with title_id %s", try_title_id)
                    if title_id is not None:
                        break
                logging.info("title_id: %s", title_id)
                try:
                    search_result = search_title_id(trophy_title.title_name)
                    logging.info("api search id = %s", search_result)
                except Exception as e:
                    logging.warning("api search error: %s", e)
                if title_id is not None:
                    all_trophy_names = psnawp.game_title(
                        title_id=title_id, platform=PlatformType(platform), account_id=friend.account_id, np_communication_id=np_communication_id
                    )
                    for trophy in all_trophy_names.trophies():
                        for received_trophy in received_trophies[3:]:
                            if received_trophy["trophy_id"] == trophy.trophy_id:
                                received_trophy["trophy_name"] = trophy.trophy_name
                                received_trophy["description"] = trophy.trophy_detail
                                received_trophy["icon"] = trophy.trophy_icon_url
                                break

                if len(received_trophies) > 3:
                    notifications.append(received_trophies)

                # Cache platinum if one was just earned (reuse game_trophies already fetched)
                has_plat = any(t.get("trophytype") == "PLATINUM" for t in received_trophies[3:])
                if has_plat:
                    time_spent = sorted([t.earned_date_time for t in game_trophies if t.earned_date_time is not None])
                    time_delta = (time_spent[-1] - time_spent[0]) if len(time_spent) >= 2 else None
                    cache_platinum(np_communication_id, friend.online_id, progress, time_delta)
            else:
                logging.info("no new trophies")
                break

    return notifications


async def friends_check():
    """Poll friends for new trophies in a thread, then send notifications."""
    notifications = await asyncio.to_thread(_poll_friend_trophies)
    for received_trophies in notifications:
        await notify_trophies(received_trophies)


# check if user in DB
def check_if_user_in_db(login):
    if users_collection.find_one({"_id": login}) is None:
        return False
    else:
        return True


# add user to DB
def add_user(login):
    users_collection.insert_one({"_id": login, "date_added": datetime.now(timezone.utc), "games": []})
    logging.info("User added to DB")


# add game to collection all_stats
def add_game_to_collection(
    npCommunicationId,
    trophyTitleName,
    login,
    progress,
    trophyTitlePlatform,
    trophyTitleIconUrl,
):
    values = {
        "_id": npCommunicationId,
        "title": trophyTitleName,
        "user progress": [{login: progress}],
        "title platform": trophyTitlePlatform,
        "image": trophyTitleIconUrl,
    }
    all_stats.insert_one(values)


# update game in collection all_stats
def update_game_in_collection(npCommunicationId, login, progress):
    all_stats.update_one({"_id": npCommunicationId}, {"$pull": {"user progress": {login: {"$exists": True}}}})
    all_stats.update_one({"_id": npCommunicationId}, {"$push": {"user progress": {login: progress}}})


def _refresh_game_progress_for_user(login, np_communication_id, progress):
    """Update stored progress for a user/game, used after DLC changes trophy counts."""
    update_game_in_collection(np_communication_id, login, progress)


def refresh_all_progress(login):
    """Re-fetch progress for all games of a user from PSN to catch DLC changes."""
    psn_user = psnawp.user(online_id=login)
    for trophy_title in psn_user.trophy_titles(limit=None):
        np_communication_id = trophy_title.np_communication_id
        progress = trophy_title.progress
        game_doc = all_stats.find_one({"_id": np_communication_id})
        if game_doc is not None:
            # Check if progress changed (DLC added or trophies earned offline)
            current_progress = None
            for user_entry in game_doc.get("user progress", []):
                if login in user_entry:
                    current_progress = user_entry[login]
                    break
            if current_progress != progress:
                _refresh_game_progress_for_user(login, np_communication_id, progress)
                logging.info("Progress updated for %s in %s: %s -> %s", login, trophy_title.title_name, current_progress, progress)
        sleep(0.1)
    logging.info("Progress refresh complete for %s", login)


def _progress_for_user(game, login):
    """Return a user's stored progress for a game document."""
    for user_entry in game.get("user progress", []):
        if login in user_entry:
            return user_entry[login]
    return None


def _format_timedelta(seconds):
    if seconds is None:
        return None
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h"


def _build_user_stats(login):
    """Build DB-backed stats for a tracked PSN username."""
    user_doc = users_collection.find_one({"_id": login})
    if user_doc is None:
        return None

    games = list(all_stats.find({"user progress": {"$elemMatch": {login: {"$exists": True}}}}))
    games_by_normalized_title = _games_by_normalized_title(_load_crossgen_context(games))
    if not games:
        return {
            "login": login,
            "played_games": 0,
            "platinums": 0,
            "completed_games": 0,
            "in_progress_games": 0,
            "not_started_games": 0,
            "average_progress": 0,
            "highest_progress": [],
            "lowest_progress": [],
            "platform_counts": {},
            "fastest_platinum": None,
            "slowest_platinum": None,
            "last_seen": user_doc.get("date_added"),
        }

    progress_rows = []
    platform_counts = {}
    platinum_rows = []

    for game in games:
        progress = _progress_for_user(game, login)
        if progress is None:
            continue
        platform = game.get("title platform", "?")
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        progress_rows.append(
            {
                "title": game.get("title", "Unknown game"),
                "platform": platform,
                "progress": progress,
            }
        )
        platinum = game.get("platinum_cache", {}).get(login)
        if platinum:
            autopop = _is_crossgen_autopop_platinum(game, login, games_by_normalized_title)
            platinum_rows.append(
                {
                    "title": game.get("title", "Unknown game"),
                    "platform": platform,
                    "seconds": platinum.get("time_delta_seconds"),
                    "autopop": autopop,
                }
            )

    completed_games = [row for row in progress_rows if row["progress"] == 100]
    in_progress_games = [row for row in progress_rows if 0 < row["progress"] < 100]
    not_started_games = [row for row in progress_rows if row["progress"] == 0]
    highest_progress = sorted(in_progress_games, key=lambda row: row["progress"], reverse=True)[:3]
    lowest_progress = sorted(in_progress_games, key=lambda row: row["progress"])[:3]
    timed_platinums = [row for row in platinum_rows if _is_rankable_platinum_seconds(row["seconds"]) and not row["autopop"]]

    return {
        "login": login,
        "played_games": len(progress_rows),
        "platinums": len(platinum_rows),
        "completed_games": len(completed_games),
        "in_progress_games": len(in_progress_games),
        "not_started_games": len(not_started_games),
        "average_progress": round(mean([row["progress"] for row in progress_rows]), 1) if progress_rows else 0,
        "highest_progress": highest_progress,
        "lowest_progress": lowest_progress,
        "platform_counts": dict(sorted(platform_counts.items())),
        "fastest_platinum": min(timed_platinums, key=lambda row: row["seconds"]) if timed_platinums else None,
        "slowest_platinum": max(timed_platinums, key=lambda row: row["seconds"]) if timed_platinums else None,
        "last_seen": user_doc.get("date_added"),
    }


def _format_stats(stats):
    lines = [
        f"Stats for {stats['login']}",
        "",
        f"Played games: {stats['played_games']}",
        f"Platinums: {stats['platinums']}",
        f"100% games: {stats['completed_games']}",
        f"In progress: {stats['in_progress_games']}",
        f"Not started: {stats['not_started_games']}",
        f"Average progress: {stats['average_progress']}%",
    ]

    if stats["platform_counts"]:
        platform_text = ", ".join(f"{platform}: {count}" for platform, count in stats["platform_counts"].items())
        lines.append(f"Platforms: {platform_text}")

    if stats["fastest_platinum"]:
        fastest = stats["fastest_platinum"]
        lines.append(f"Fastest platinum: {fastest['title']} ({_format_timedelta(fastest['seconds'])})")
    if stats["slowest_platinum"] and stats["slowest_platinum"] != stats["fastest_platinum"]:
        slowest = stats["slowest_platinum"]
        lines.append(f"Slowest platinum: {slowest['title']} ({_format_timedelta(slowest['seconds'])})")

    if stats["highest_progress"]:
        lines.append("")
        lines.append("Closest unfinished:")
        for row in stats["highest_progress"]:
            lines.append(f"- {row['title']} ({row['platform']}): {row['progress']}%")

    if stats["lowest_progress"]:
        lines.append("")
        lines.append("Lowest started:")
        for row in stats["lowest_progress"]:
            lines.append(f"- {row['title']} ({row['platform']}): {row['progress']}%")

    if stats["last_seen"]:
        lines.append("")
        lines.append(f"Last trophy checkpoint: {stats['last_seen'].strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


# adds percentage stats to table for 1 user (blocking — run via asyncio.to_thread)
def add_all_user_games(login):

    psn_user = psnawp.user(online_id=login)
    for trophy_title in psn_user.trophy_titles(limit=None):
        game_name = trophy_title.title_name
        np_communication_id = trophy_title.np_communication_id
        platform = next(iter(trophy_title.title_platform)).value
        progress = trophy_title.progress
        icon_url = trophy_title.title_icon_url
        # if game not in DB
        if all_stats.find_one({"_id": np_communication_id}) is None:
            add_game_to_collection(np_communication_id, game_name, login, progress, platform, icon_url)
            logging.info(f"{game_name} added to DB")
            sleep(0.1)
        else:
            update_game_in_collection(np_communication_id, login, progress)
            logging.info(f"{game_name} updated in DB")
            sleep(0.1)

        # Cache platinum if earned
        earned = trophy_title.earned_trophies
        plat_count = earned.platinum if isinstance(earned, TrophySet) else earned.get("platinum", 0)
        if plat_count == 1:
            # Check if already cached to avoid redundant trophy list fetch
            existing = all_stats.find_one({"_id": np_communication_id})
            already_cached = existing and existing.get("platinum_cache", {}).get(login)
            if not already_cached:
                time_delta = None
                try:
                    game_trophies = psn_user.trophies(
                        np_communication_id=np_communication_id,
                        platform=PlatformType(platform),
                        include_progress=True,
                        trophy_group_id="all",
                    )
                    time_spent = sorted([t.earned_date_time for t in game_trophies if t.earned_date_time is not None])
                    if len(time_spent) >= 2:
                        time_delta = time_spent[-1] - time_spent[0]
                except Exception as e:
                    logging.warning("Error caching platinum time for %s / %s: %s", login, game_name, e)
                cache_platinum(np_communication_id, login, progress, time_delta)

        sleep(0.5)
    logging.info(f"all {login}'s games added to DB")


# add user to DB
@dp.message_handler(commands=["add"])
async def cmd_add(message):
    parts = message.text.split()
    if len(parts) < 2:
        await bot.send_message(message.chat.id, "Usage: /add <PSN username>")
        return
    try:
        login = _clean_username_arg(parts[1])
        user_account_id = psnawp.user(online_id=login)
        friend = user_account_id.friendship()
        friend_relation = friend["friendRelation"]

        if friend_relation == "friend":
            if not check_if_user_in_db(login):
                await bot.send_message(message.chat.id, f"Adding {login} to DB")
                add_user(login)
                _link_telegram_user(login, message)
                await asyncio.to_thread(add_all_user_games, login)
                await bot.send_message(message.chat.id, f"{login} added successfully")
            else:
                _link_telegram_user(login, message)
                await bot.send_message(message.chat.id, f"{login} already in DB")
        else:
            await bot.send_message(message.chat.id, "Become friends with MillerUSACC first!")

    except Exception as e:
        logging.error(f"Error in /add: {e}")
        await bot.send_message(message.chat.id, "Something went wrong")


# looking for a game in the database
def find_game(game):
    query = {"title": re.compile(re.escape(game), re.IGNORECASE)}
    res = all_stats.find(query)
    return list(res)


# подготовить список из данных одной игры
def make_single_list(list_of_games):
    found_game = []
    temp_d = dict(list_of_games)
    for keys, vals in temp_d.items():
        if keys in ("image", "title", "user progress", "title platform"):
            found_game.append(vals)
    return found_game


# get title ids by name from csv file
def get_title_ids_by_name(name: str) -> List[str]:
    if name == "Alan Wake II":
        name = "Alan Wake 2"
    # in name remove ® and ™
    name = name.replace("®", "")
    name = name.replace("™", "")
    name = name.replace("’", "'")

    # remove spaces and new lines from name
    name = name.strip()

    title_ids = []
    tsv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PlayStation-Titles", "All_Titles.tsv")
    if not os.path.exists(tsv_path):
        return title_ids
    with open(tsv_path, newline="") as tsvfile:
        reader = csv.DictReader(tsvfile, delimiter="\t")
        for row in reader:
            # normalize row name without mutating the original row
            row_name = row["name"].replace("®", "")
            row_name = row_name.replace("™", "")
            row_name = row_name.replace("\u2019", "'")

            # Exact match gets highest priority
            if name.lower() == row_name.lower():
                title_ids.insert(0, row["titleId"])
            elif name.lower() in row_name.lower():
                title_ids.append(row["titleId"])
            elif len(name) > 5 and levenshtein_distance(name.lower(), row_name.lower()) < 3:
                title_ids.append(row["titleId"])
            if name.lower() == "warhammer - chaosbane":
                title_ids.append("PPSA01445_00")
                title_ids.append("PPSA01446_00")
                title_ids.append("PPSA01447_00")
                title_ids.append("PPSA11410_00")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for tid in title_ids:
        if tid not in seen:
            seen.add(tid)
            deduped.append(tid)
    return deduped


# cache platinum data for a user in a game document
def cache_platinum(np_communication_id, user_name, progress, time_delta):
    """Store platinum status and time-to-finish in the game document."""
    seconds = int(time_delta.total_seconds()) if time_delta else None
    all_stats.update_one(
        {"_id": np_communication_id},
        {"$set": {f"platinum_cache.{user_name}": {"progress": progress, "time_delta_seconds": seconds}}},
    )
    logging.info("Cached platinum for %s in %s (%s seconds)", user_name, np_communication_id, seconds)


# check if user has platinum trophy in game (DB-only, no API calls)
def check_platinum(game, np_communication_id, platform):
    result = []
    platinum_cache = game.get("platinum_cache", {})
    games_by_normalized_title = _games_by_normalized_title(_load_crossgen_context([game]))

    for users in game["user progress"]:
        for user_name, progress in users.items():
            cached = platinum_cache.get(user_name)
            if cached:
                user_data = [user_name, progress, 1]
                seconds = cached.get("time_delta_seconds")
                if seconds is not None:
                    user_data.append(timedelta(seconds=seconds))
                _mark_autopop_row(user_data, _is_crossgen_autopop_platinum(game, user_name, games_by_normalized_title))
                result.append(user_data)
            else:
                result.append([user_name, progress, 0, 0])

    return result


def make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters):
    str_with_progress_and_emoji = ""
    for item in sorted_list:
        fire = ""
        animal = ""
        if item[2] == 1 and _platinum_duration(item) is not None and sorted_platinum_hunters and not _is_autopop_row(item):
            fire = "🏆"
            if _platinum_duration(item) == _platinum_duration(sorted_platinum_hunters[0]):
                animal = "🐇"
            elif _platinum_duration(item) == _platinum_duration(sorted_platinum_hunters[-1]):
                animal = "🐢"
        elif item[2] == 1:
            fire = "🏆"

        str_with_progress_and_emoji += f"{item[0]}: {item[1]}{fire}{animal}\n"
    return str_with_progress_and_emoji


# make string from completage document
def compose_answer(game):
    logging.info("game = %s", game)
    trophy_list = check_platinum(game, game["_id"], game["title platform"])
    if trophy_list is None:
        return None
    logging.info("trophy_list = %s", trophy_list)
    platinum_hunters = []
    other_users = []
    for item in trophy_list:
        # print(item)
        if item[2] == 1:
            platinum_hunters.append(item)
        else:
            other_users.append(item)

    logging.info("platinum_hunters = %s", platinum_hunters)
    logging.info("other_users = %s", other_users)

    ranked_platinum_hunters = [item for item in platinum_hunters if _is_rankable_platinum_row(item)]
    unranked_platinum_hunters = [item for item in platinum_hunters if item not in ranked_platinum_hunters]
    sorted_platinum_hunters = sorted(ranked_platinum_hunters, key=_platinum_sort_seconds) + unranked_platinum_hunters
    sorted_other_users = sorted(other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = make_str_with_progress_and_emoji(sorted_list, ranked_platinum_hunters)
    logging.info(users)
    return {
        "title": game["title"],
        "platform": [game["title platform"]],
        "users": users,
        "sorted_platinum_hunters": sorted_platinum_hunters,
        "sorted_other_users": sorted_other_users,
    }


def compose_answer_group(games_ids: list = []):
    logging.info("games_ids = %s", games_ids)
    group_result = []
    for game_id in games_ids:

        query = {"_id": game_id}
        game = all_stats.find_one(query)
        if game is not None:
            group_result.append(compose_answer(game))

    logging.info("group_result = %s", group_result)
    for item in group_result:
        platform = item["platform"][0]
        for player in item["sorted_platinum_hunters"]:
            player[0] = platform + " " + player[0]
        for player in item["sorted_other_users"]:
            player[0] = platform + " " + player[0]

    # Group the dictionaries with the same title and platform
    grouped_records = []
    for key, group in groupby(group_result, key=lambda x: x["title"]):
        dicts = list(group)
        combined_dict = {
            "title": key,
            "platform": list(chain(*[d["platform"] for d in dicts])),
            "sorted_platinum_hunters": list(chain(*[d["sorted_platinum_hunters"] for d in dicts])),
            "sorted_other_users": list(chain(*[d["sorted_other_users"] for d in dicts])),
        }
        grouped_records.append(combined_dict)

    group_result = grouped_records

    # Sort the grouped records by the time it took the fastest platinum hunter to get the platinum
    sorted_platinum_hunters = group_result[0]["sorted_platinum_hunters"]
    sorted_other_users = group_result[0]["sorted_other_users"]
    ranked_platinum_hunters = [item for item in sorted_platinum_hunters if _is_rankable_platinum_row(item)]
    unranked_platinum_hunters = [item for item in sorted_platinum_hunters if item not in ranked_platinum_hunters]
    sorted_platinum_hunters = sorted(ranked_platinum_hunters, key=_platinum_sort_seconds) + unranked_platinum_hunters
    sorted_other_users = sorted(sorted_other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = make_str_with_progress_and_emoji(sorted_list, ranked_platinum_hunters)
    return {
        "title": group_result[0]["title"],
        "platform": group_result[0]["platform"],
        "users": users,
        "sorted_platinum_hunters": sorted_platinum_hunters,
        "sorted_other_users": sorted_other_users,
    }


# start search in db
@dp.message_handler(commands=["find"])
async def cmd_find(message):
    parts = message.text.split()[1:]
    if not parts:
        await bot.send_message(message.chat.id, "Usage: /find <game name>")
        return
    name = " ".join(parts)
    result = find_game(name)
    if len(result) == 0:
        await bot.send_message(message.chat.id, "Game not found")
    elif len(result) == 1:
        game = result[0]
        games_dict = compose_answer(game)
        answer = f"{games_dict['title']} {games_dict['platform'][0]}\n\n\n{games_dict['users']}"
        logging.info(answer)
        logging.info(games_dict)
        if answer is None:
            await bot.send_message(message.chat.id, "No trophies for this game")
        else:
            await bot.send_photo(message.chat.id, game["image"], answer)
    else:

        games_by_title = {}
        for game in result:
            title = game["title"]
            platform = game["title platform"]
            game_id = game["_id"]
            progress = game["user progress"]
            image = game["image"]

            if title not in games_by_title:
                games_by_title[title] = {"title": title, "platform": [platform], "ids": [game_id], "progress": progress, "image": image}
            else:
                games_by_title[title]["ids"].append(game_id)
                games_by_title[title]["platform"].append(platform)
                games_by_title[title]["progress"] += progress

        games_list_grouped = list(games_by_title.values())
        for game in games_list_grouped:
            logging.info(game)

        # if games_list_grouped has only one game, send it
        if len(games_list_grouped) == 1:
            game = games_list_grouped[0]
            games_dict = compose_answer_group(game["ids"])
            answer = f"{games_dict['title']} {' '.join(games_dict['platform'])}\n\n\n{games_dict['users']}"
            #!!!!compose_answer_group
            if answer is None:
                await bot.send_message(message.chat.id, "No trophies for this game")
            else:
                await bot.send_photo(message.chat.id, game["image"], answer)
            return
        else:
            # if games_list_grouped has more than one game, send inline keyboard
            keyboard = types.InlineKeyboardMarkup()
            for game in games_list_grouped:
                game_id_str = ",".join(game["ids"])
                cb_data = _store_callback_data(game_id_str)
                keyboard.add(types.InlineKeyboardButton(text=game["title"], callback_data=cb_data))
            await bot.send_message(message.chat.id, "Choose game:", reply_markup=keyboard)
            return


# callback for inline keyboard
@dp.callback_query_handler(lambda c: True)
async def process_callback_button1(callback_query: types.CallbackQuery):
    raw_data = _resolve_callback_data(callback_query.data)
    logging.info("callback data = %s", raw_data)
    list_of_ids = raw_data.split(",")

    # get game from db by np_communication_id
    if len(list_of_ids) == 1:
        game = all_stats.find_one({"_id": list_of_ids[0]})
        # if game not found
        if game is None:
            await bot.send_message(callback_query.message.chat.id, "Game not found")
            await bot.answer_callback_query(callback_query.id)
            return
        # if game found
        games_dict = compose_answer(game)
        answer = f"{games_dict['title']} {games_dict['platform'][0]}\n\n\n{games_dict['users']}"

        await bot.send_photo(callback_query.message.chat.id, game["image"], answer)
        await bot.answer_callback_query(callback_query.id)
        return
    else:
        game = all_stats.find_one({"_id": list_of_ids[0]})

        games_dict = compose_answer_group(list_of_ids)
        answer = f"{games_dict['title']} {' '.join(games_dict['platform'])}\n\n\n{games_dict['users']}"
        if answer is None:
            await bot.send_message(callback_query.message.chat.id, "No trophies for this game")
            await bot.answer_callback_query(callback_query.id)
        else:
            await bot.send_photo(callback_query.message.chat.id, game["image"], answer)
            await bot.answer_callback_query(callback_query.id)
        return


@dp.message_handler(commands=["test"])
async def test(message):

    # await send_trophy("gorcheque", "Sackboy", "PS4", {"trophy_name": "Test", "description": "Test desc", "percentage": 50, "trophytype": "GOLD", "icon": ""})
    res = users_collection.find()
    for i in res:
        logging.info(i)

    res = all_stats.find()
    for i in res[:2]:
        logging.info(i)
    # await friends_check()


@dp.message_handler(commands=["erase"])
async def erase(message):
    if message.chat.id == 46051043:
        all_stats.delete_many({})
        users_collection.delete_many({})
        await bot.send_message(message.chat.id, "DB erased")


def delete_user(username):

    # Remove the user from the first collection
    all_stats.update_many({}, {"$pull": {"user progress": {username: {"$exists": True}}}})

    # Remove the user from the second collection
    users_collection.delete_one({"_id": username})

    logging.info(f"User {username} deleted from both tables.")


@dp.message_handler(commands=["del"])
async def cmd_del(message):
    if message.chat.id == 46051043:
        parts = message.text.split()
        if len(parts) < 2:
            await bot.send_message(message.chat.id, "Usage: /del <username>")
            return
        login = parts[1]
        delete_user(login)
        await bot.send_message(message.chat.id, f"{login} deleted")


@dp.message_handler(commands=["refresh"])
async def cmd_refresh(message):
    """Re-fetch completion progress for all tracked users. Catches DLC percentage changes."""
    if message.chat.id != 46051043:
        return
    await bot.send_message(message.chat.id, "Refreshing progress for all users...")
    users = list(users_collection.find())
    for user_doc in users:
        login = user_doc["_id"]
        try:
            await asyncio.to_thread(refresh_all_progress, login)
            await bot.send_message(message.chat.id, f"✓ {login}")
        except Exception as e:
            logging.error("Error refreshing %s: %s", login, e)
            await bot.send_message(message.chat.id, f"✗ {login}: {e}")
    await bot.send_message(message.chat.id, "Progress refresh complete")


@dp.message_handler(commands=["rebuild"])
async def cmd_rebuild(message):
    """Re-import ALL games for every tracked user. Use after long downtime."""
    if message.chat.id != 46051043:
        return
    users = list(users_collection.find())
    if not users:
        await bot.send_message(message.chat.id, "No tracked users")
        return
    await bot.send_message(message.chat.id, f"Rebuilding games DB for {len(users)} users...")
    # Reset timestamps so background check won't spam notifications
    users_collection.update_many({}, {"$set": {"date_added": datetime.now(timezone.utc)}})
    for user_doc in users:
        login = user_doc["_id"]
        try:
            await asyncio.to_thread(add_all_user_games, login)
            await bot.send_message(message.chat.id, f"✓ {login}")
        except Exception as e:
            logging.error("Error rebuilding %s: %s", login, e)
            await bot.send_message(message.chat.id, f"✗ {login}: {e}")
    await bot.send_message(message.chat.id, "Rebuild complete")


def _cache_platinums_for_user(login):
    """Scan a user's trophy titles and cache any uncached platinums. Returns count cached."""
    psn_user = psnawp.user(online_id=login)
    cached_count = 0
    for trophy_title in psn_user.trophy_titles(limit=None):
        earned = trophy_title.earned_trophies
        plat_count = earned.platinum if isinstance(earned, TrophySet) else earned.get("platinum", 0)
        if plat_count != 1:
            continue
        np_comm_id = trophy_title.np_communication_id
        platform = next(iter(trophy_title.title_platform)).value
        progress = trophy_title.progress
        # Skip if already cached
        game_doc = all_stats.find_one({"_id": np_comm_id}, {"platinum_cache": 1})
        if game_doc and game_doc.get("platinum_cache", {}).get(login):
            continue
        # Fetch trophies only for this uncached platinum
        time_delta = None
        try:
            game_trophies = psn_user.trophies(
                np_communication_id=np_comm_id,
                platform=PlatformType(platform),
                include_progress=True,
                trophy_group_id="all",
            )
            time_spent = sorted([t.earned_date_time for t in game_trophies if t.earned_date_time is not None])
            if len(time_spent) >= 2:
                time_delta = time_spent[-1] - time_spent[0]
        except Exception as e:
            logging.warning("Error getting trophy times for %s / %s: %s", login, np_comm_id, e)
        cache_platinum(np_comm_id, login, progress, time_delta)
        cached_count += 1
        sleep(0.5)
    return cached_count


async def run_cache_platinums(send_updates=False, chat_id=None):
    if _cache_lock.locked():
        logging.info("Platinum cache run skipped; another cache run is already active")
        if send_updates and chat_id:
            await bot.send_message(chat_id, "Cache is already running")
        return None

    async with _cache_lock:
        users = list(users_collection.find())
        if not users:
            logging.info("Platinum cache run skipped; no tracked users")
            if send_updates and chat_id:
                await bot.send_message(chat_id, "No tracked users")
            return 0

        logging.info("Starting platinum cache run for %s users", len(users))
        if send_updates and chat_id:
            await bot.send_message(chat_id, f"Caching platinums for {len(users)} users...")

        total = 0
        for user_doc in users:
            login = user_doc["_id"]
            try:
                async with _psn_job_lock:
                    count = await asyncio.to_thread(_cache_platinums_for_user, login)
                total += count
                logging.info("Cached %s new platinums for %s", count, login)
                if send_updates and chat_id:
                    await bot.send_message(chat_id, f"✓ {login}: {count} new")
            except Exception as e:
                logging.error("Error caching platinums for %s: %s", login, e, exc_info=True)
                if send_updates and chat_id:
                    await bot.send_message(chat_id, f"✗ {login}: {e}")

        logging.info("Platinum cache run complete; cached %s platinums", total)
        if send_updates and chat_id:
            await bot.send_message(chat_id, f"Done — cached {total} platinums")
        return total


@dp.message_handler(commands=["cache"])
async def cmd_cache(message):
    """Scan all tracked users and cache any uncached platinum trophies (fast — skips already cached)."""
    if message.chat.id != 46051043:
        return
    await run_cache_platinums(send_updates=True, chat_id=message.chat.id)


@dp.message_handler(commands=["psn_search"])
async def cmd_psn_search(message):
    """Search PSN for users by partial name."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(message.chat.id, "Usage: /psn_search <username>")
        return
    query = parts[1].strip()
    try:
        results = []
        for result in psnawp.search(search_query=query, search_domain=SearchDomain.USERS, limit=10):
            social_metadata = result.get("socialMetadata", {})
            online_id = social_metadata.get("onlineId", "?")
            relation = social_metadata.get("relationshipState", "")
            highlight = social_metadata.get("highlights", {}).get("onlineId", [online_id])
            display = highlight[0] if highlight else online_id
            marker = " ✓" if relation == "friend" else ""
            results.append(f"• {display}{marker}")
        if results:
            await bot.send_message(message.chat.id, "PSN users found:\n" + "\n".join(results))
        else:
            await bot.send_message(message.chat.id, "No users found")
    except Exception as e:
        logging.error("Error in /psn_search: %s", e)
        await bot.send_message(message.chat.id, f"Search error: {e}")


@dp.message_handler(commands=["stats"])
async def cmd_stats(message):
    """Show DB-backed stats for a tracked PSN username."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(message.chat.id, "Usage: /stats <PSN username>")
        return
    login = _resolve_tracked_login(parts[1])
    if login is None:
        await bot.send_message(message.chat.id, "User not tracked. Add them first with /add <PSN username>")
        return
    stats = _build_user_stats(login)
    await bot.send_message(message.chat.id, _format_stats(stats))


@dp.message_handler(commands=["me"])
async def cmd_me(message):
    """Show DB-backed stats for the Telegram user's linked PSN username."""
    login = _resolve_me_login(message)
    if login is None:
        await bot.send_message(
            message.chat.id,
            "I don't know your PSN username yet. Use /stats <PSN username>, or run /add <PSN username> once to link it.",
        )
        return
    stats = _build_user_stats(login)
    await bot.send_message(message.chat.id, _format_stats(stats))


@dp.message_handler(commands=["status"])
async def cmd_status(message):
    """Show online status and current game for all tracked users."""
    users = list(users_collection.find())
    if not users:
        await bot.send_message(message.chat.id, "No tracked users")
        return
    lines = []
    for user_doc in users:
        login = user_doc["_id"]
        try:
            psn_user = psnawp.user(online_id=login)
            game = get_user_presence(psn_user)
            if game:
                lines.append(f"🟢 {login}: {game}")
            else:
                lines.append(f"⚫ {login}")
        except Exception:
            lines.append(f"❓ {login}")
    await bot.send_message(message.chat.id, "\n".join(lines))


async def background_on_start() -> None:
    """background task which is created when bot starts"""

    while True:
        try:
            async with _psn_job_lock:
                await friends_check()
        except Exception as e:
            logging.error("Error in friends_check: %s", e, exc_info=True)
        await asyncio.sleep(350 + random.randint(1, 10))


def _seconds_until_daily_cache() -> float:
    now = datetime.now(timezone.utc)
    next_run = now.replace(hour=DAILY_CACHE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


async def daily_cache_on_schedule() -> None:
    """Run platinum cache once per day without Telegram output."""
    while True:
        delay = _seconds_until_daily_cache()
        logging.info("Next silent platinum cache run in %.0f seconds", delay)
        await asyncio.sleep(delay)
        try:
            await run_cache_platinums(send_updates=False)
        except Exception as e:
            logging.error("Error in scheduled platinum cache: %s", e, exc_info=True)
        await asyncio.sleep(60)


async def on_bot_start_up(dispatcher: Dispatcher) -> None:
    """List of actions which should be done before bot start"""
    asyncio.create_task(background_on_start())  # creates background task
    asyncio.create_task(daily_cache_on_schedule())


executor.start_polling(dp, on_startup=on_bot_start_up)
