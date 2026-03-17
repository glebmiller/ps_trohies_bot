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
            # result["id"] format: "conceptId:productId" e.g. "201930:UP1004-CUSA00419_00-GTAVDIGITALDOWNL"
            result_id = result.get("id", "")
            # Try to extract CUSA/PPSA style title_id from the product portion
            parts = result_id.split(":")
            if len(parts) >= 2:
                product_id = parts[1]
                # Extract title_id pattern like CUSA00419_00 or PPSA01234_00
                match = re.search(r"((?:CUSA|PPSA|NPWR)\d+_\d+)", product_id)
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

                game_trophies = psn_user.trophies(np_communication_id=np_communication_id, platform=PlatformType(platform), include_progress=True)

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

                    notifications.append(received_trophies)

                # Cache platinum if one was just earned
                has_plat = any(t.get("trophytype") == "PLATINUM" for t in received_trophies[3:])
                if has_plat:
                    try:
                        all_earned = psn_user.trophies(
                            np_communication_id=np_communication_id,
                            platform=PlatformType(platform),
                            include_progress=True,
                        )
                        time_spent = sorted([t.earned_date_time for t in all_earned if t.earned_date_time is not None])
                        time_delta = (time_spent[-1] - time_spent[0]) if len(time_spent) >= 2 else None
                        cache_platinum(np_communication_id, friend.online_id, progress, time_delta)
                    except Exception as e:
                        logging.warning("Failed to cache platinum for %s: %s", friend.online_id, e)
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
        login = parts[1]
        user_account_id = psnawp.user(online_id=login)
        friend = user_account_id.friendship()
        friend_relation = friend["friendRelation"]

        if friend_relation == "friend":
            if not check_if_user_in_db(login):
                await bot.send_message(message.chat.id, f"Adding {login} to DB")
                add_user(login)
                await asyncio.to_thread(add_all_user_games, login)
                await bot.send_message(message.chat.id, f"{login} added successfully")
            else:
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


# check if user has platinum trophy in game
def check_platinum(game, np_communication_id, platform):
    result = []
    name = game["title"]
    platinum_cache = game.get("platinum_cache", {})

    # Collect users that need API calls vs cached
    users_needing_api = []
    for users in game["user progress"]:
        for user_name in users.keys():
            cached = platinum_cache.get(user_name)
            if cached:
                # Platinum is permanent — use cached data, but use live progress from DB
                user_data = [user_name, users[user_name], 1]
                seconds = cached.get("time_delta_seconds")
                if seconds is not None:
                    user_data.append(timedelta(seconds=seconds))
                result.append(user_data)
                logging.info("Using cached platinum for %s", user_name)
            else:
                users_needing_api.append((user_name, users[user_name]))

    if not users_needing_api:
        return result

    # Only do title_id lookups if we have users that need API calls
    title_ids = get_title_ids_by_name(name)
    logging.info("title_ids= %s", title_ids)

    if not title_ids:
        try:
            api_title_id = search_title_id(name)
            if api_title_id:
                title_ids.append(api_title_id)
        except Exception as e:
            logging.warning("search API fallback failed: %s", e)

    title_ids.reverse()
    shared_title_id = None

    for user_name, db_progress in users_needing_api:
        logging.info("user_name = %s", user_name)
        user_data = [user_name]
        psn_user = psnawp.user(online_id=user_name)

        # Try title_id-based lookup first (gives progress + earned_trophies in one call)
        ids_to_try = []
        if shared_title_id:
            ids_to_try = [shared_title_id] + [t for t in title_ids if t != shared_title_id]
        else:
            ids_to_try = list(title_ids)

        user_trophy_info = None
        user_title_id = None
        game_trophies = None
        if ids_to_try:
            try:
                for trophy_title_info in psn_user.trophy_titles_for_title(title_ids=ids_to_try):
                    user_title_id = trophy_title_info.title_id if hasattr(trophy_title_info, "title_id") else ids_to_try[0]
                    user_trophy_info = trophy_title_info
                    if shared_title_id is None:
                        shared_title_id = user_title_id
                    break
            except Exception:
                logging.info("No trophies for %s with title_ids %s", user_name, ids_to_try)

        if user_trophy_info is not None:
            # Got data from title_id lookup
            user_data.append(user_trophy_info.progress)
            earned = user_trophy_info.earned_trophies
            plat_count = earned.platinum if isinstance(earned, TrophySet) else earned.get("platinum", 0)
        else:
            # Fallback: use np_communication_id directly to check platinum
            logging.info("No title_id for %s / %s, falling back to np_communication_id", user_name, name)
            user_data.append(db_progress)
            plat_count = 0
            try:
                game_trophies = list(
                    psn_user.trophies(
                        np_communication_id=np_communication_id,
                        platform=PlatformType(platform),
                        include_progress=True,
                    )
                )
                for t in game_trophies:
                    if t.trophy_type.name == "PLATINUM" and t.earned_date_time is not None:
                        plat_count = 1
                        break
            except Exception as e:
                logging.warning("Fallback trophy check failed for %s: %s", user_name, e)

        if plat_count == 1:
            user_data.append(1)
            logging.info("Platinum")
        else:
            user_data.append(0)
            user_data.append(0)
            logging.info("No Platinum")

        if user_data[2] == 1:
            user_np_comm_id = (user_trophy_info.np_communication_id if user_trophy_info else None) or np_communication_id
            time_delta = None
            try:
                if game_trophies is None:
                    # Need to fetch trophies for time calculation
                    game_trophies = list(
                        psn_user.trophies(
                            np_communication_id=user_np_comm_id,
                            platform=PlatformType(platform),
                            include_progress=True,
                        )
                    )
                # else: game_trophies already fetched in fallback above
                time_spent = [t.earned_date_time for t in game_trophies if t.earned_date_time is not None]
                time_spent = sorted(time_spent)
                if len(time_spent) >= 2:
                    time_delta = time_spent[-1] - time_spent[0]
                    user_data.append(time_delta)
            except Exception as e:
                logging.warning("Error getting trophy times for %s: %s", user_name, e)

            # Cache the platinum so we never call the API for this user/game again
            cached_progress = user_trophy_info.progress if user_trophy_info else db_progress
            cache_platinum(np_communication_id, user_name, cached_progress, time_delta)

        result.append(user_data)
    return result


def make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters):
    str_with_progress_and_emoji = ""
    for item in sorted_list:
        fire = ""
        animal = ""
        if item[2] == 1 and len(item) > 3 and sorted_platinum_hunters:
            fire = "🏆"
            if item[3].days == sorted_platinum_hunters[0][3].days:
                animal = "🐇"
            elif item[3].days == sorted_platinum_hunters[-1][3].days:
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

    sorted_platinum_hunters = sorted(platinum_hunters, key=lambda x: x[3].days) if platinum_hunters else []
    sorted_other_users = sorted(other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters)
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
        res = all_stats.find(query)
        if res is not None:
            group_result.append(compose_answer(res[0]))

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
    sorted_platinum_hunters = sorted(sorted_platinum_hunters, key=lambda x: x[3].days) if sorted_platinum_hunters else []
    sorted_other_users = sorted(sorted_other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters)
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
        games_dict = await asyncio.to_thread(compose_answer, game)
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
            games_dict = await asyncio.to_thread(compose_answer_group, game["ids"])
            # games_dict = compose_answer(game)
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
        games_dict = await asyncio.to_thread(compose_answer, game)
        answer = f"{games_dict['title']} {games_dict['platform'][0]}\n\n\n{games_dict['users']}"

        await bot.send_photo(callback_query.message.chat.id, game["image"], answer)
        await bot.answer_callback_query(callback_query.id)
        return
    else:
        game = all_stats.find_one({"_id": list_of_ids[0]})

        # print(callback_query.data)
        games_dict = await asyncio.to_thread(compose_answer_group, list_of_ids)
        # games_dict = compose_answer(game)
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
            await friends_check()
        except Exception as e:
            logging.error("Error in friends_check: %s", e, exc_info=True)
        await asyncio.sleep(350 + random.randint(1, 10))


async def on_bot_start_up(dispatcher: Dispatcher) -> None:
    """List of actions which should be done before bot start"""
    asyncio.create_task(background_on_start())  # creates background task


executor.start_polling(dp, on_startup=on_bot_start_up)
