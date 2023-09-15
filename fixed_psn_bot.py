
from psnawp_api import PSNAWP
from time import sleep
import json
from enum import Enum
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, executor, types
import pymongo
import re
import database
import os
import asyncio
#import telegram
import random
import logging
from subprocess import PIPE, Popen
# import inline keyboard markup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

#from telegram import ParseMode


def cmdline(command):
    process = Popen(args=command, stdout=PIPE, shell=True)
    return process.communicate()[0]


# Configure logging
logging.basicConfig(level=logging.INFO)
psnawp = PSNAWP(database.getPSNToken())

# подключаемся к БД
client = pymongo.MongoClient("mongodb://localhost:27017")
db = client.PSNTrophies_new

# общая таблица без названий трофеев
all_stats = db.games
# таблица пользователей
users_collection = db.users

# таблица игр со списком трофеев с названиями
#games_collection = db.games_with_trophies

CHATID = database.get_chat_id()

# Setting up telegram bot Wisely
TOKEN = database.getBotToken()
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)


def friends_check():
    pass

# check if user in DB
def check_if_user_in_db(login):
    if users_collection.find_one({"_id": login}) is None:
        return False
    else:
        return True
    

# add user to DB
def add_user(login):
    users_collection.insert_one({"_id": login, 'date_added': datetime.now(timezone.utc), "games": []})
    print("User added to DB")

# add game to collection all_stats
def add_game_to_collection(
    npTitleId,
    trophyTitleName,
    login,
    progress,
    trophyTitlePlatform,
    trophyTitleIconUrl,
):
    values = {
        "_id": npTitleId,
        "title": trophyTitleName,
        "user progress": [{login: progress}],
        "title platform": trophyTitlePlatform,
        "image": trophyTitleIconUrl,
    }
    all_stats.insert_one(values)

# update game in collection all_stats
def update_game_in_collection(npTitleId, login, progress):
    all_stats.update_one(
        {"_id": npTitleId},
        {
            "$push": {
                "user progress": {login: progress},
            }
        },
    )


# adds percentage stats to table for 1 user
def add_all_user_games(login):

    client = psnawp.user(online_id=login)
    for trophy_title in client.trophy_titles(limit=None):
        print(trophy_title)
        sleep(300)
        game_name = trophy_title.title_name
        np_title_id = trophy_title.np_title_id
        platform = next(iter(trophy_title.title_platform)).value
        progress = trophy_title.progress
        icon_url = trophy_title.title_icon_url
        # if game not in DB
        print(np_title_id)
        if all_stats.find_one({"_id": np_title_id}) is None:
            add_game_to_collection(np_title_id, game_name, login, progress, platform, icon_url)
            print(f"{game_name} added to DB")
            sleep(0.1)
        else:
            update_game_in_collection(np_title_id, login, progress)
            print(f"{game_name} updated in DB")
            sleep(0.1)

        # if game in DB
        # update_game_in_collection(np_title_id, game_name, login, progress, platform, icon_url)
        sleep(0.5)
    print(f"all {login}'s games added to DB")
        

# add user to DB
@dp.message_handler(commands=["add"])
async def add(message):
    login = message.text.split()[1]
    #login = extract_arg(message.text)

    user_account_id = psnawp.user(online_id=login)
    friend = user_account_id.friendship()
    print(f"{login} is {friend['friendRelation']}")
    if friend["friendRelation"] == "friend": # and not check_if_user_in_db(login):
        await bot.send_message(message.chat.id, f"adding {login} to DB")
        add_user(login)
        # load games to collection
        add_all_user_games(login)

    elif friend["friendRelation"] == "friend" and check_if_user_in_db(login):
        await bot.send_message(message.chat.id, f"{login} alredy in DB")
    else:
        await bot.send_message(
            message.chat.id, "Become friends with MillerUSACC first!"
        )

# looking for a game in the database
def find_game(game):
    query = {"title": re.compile(game, re.IGNORECASE)}
    # result = []
    res = all_stats.find(query)
    return list(res)
"""
# write a function to find a game in the database by np_title_id
def find_game_by_id(np_title_id):
    query = {"_id": np_title_id}
    res = all_stats.find(query)
    return list(res)
"""


# подготовить список из данных одной игры
def make_single_list(list_of_games):
    found_game = []
    temp_d = dict(list_of_games)
    for keys, vals in temp_d.items():
        if keys in ("image", "title", "user progress", "title platform"):
            found_game.append(vals)
    return found_game

#check if user has platinum trophy in game
def check_platinum(game):
    result = []
    for users in game['user progress']:
        
        for user_name in users.keys():
            print("user_name =", user_name)
            print()
            user_data = [user_name]
            client = psnawp.user(online_id=user_name)
            find_name_and_id = psnawp.search().get_title_id(game["title"])
            print("find_name_and_id =", find_name_and_id)
            print()
            title_id = find_name_and_id[1]
            print("title_id =", title_id)
            print()

            for trophy_title_info in client.trophy_titles_for_title(title_ids=[title_id]):
                user_data.append(trophy_title_info.progress)
                print("trophy_title_info =", trophy_title_info)
                print()
                np_title_id = trophy_title_info.np_title_id
                platform = next(iter(trophy_title_info.title_platform)).value
                

                if trophy_title_info.earned_trophies['platinum'] == 1:
                    user_data.append(1)
                    print("Platinum")
                else:
                    user_data.append(0)
                    user_data.append(0)
                    print("No Platinum")
                if user_data[2] == 1:
                    game_trophies = client.trophies(np_title_id=np_title_id, platform=platform, include_metadata=False)
                    time_spent = []
                    for single_trophy in game_trophies:
                        time_spent.append(single_trophy.earned_date_time)
                    #delete None values
                    time_spent = [x for x in time_spent if x is not None]
                    time_spent = sorted(time_spent)
                    #calculate delta of time between first and last trophy
                    delta = time_spent[-1] - time_spent[0]
                    user_data.append(delta)
                
        result.append(user_data)
    return result
    


# make string from completage document
def compose_answer(game):
    trophy_list = check_platinum(game)
    print("trophy_list =", trophy_list)
    platinum_hunters = []
    other_users = []
    for item in trophy_list:
        print(item)
        if item[2] == 1:
            platinum_hunters.append(item)
        else:
            other_users.append(item)

    sorted_platinum_hunters = sorted(platinum_hunters, key=lambda x: x[3].days)
    sorted_other_users = sorted(other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = ''
    for item in sorted_list:
        fire = ''
        animal = ''
        if item[2] == 1:
            fire = '🏆'
            if item[3].days == sorted_platinum_hunters[0][3].days:
                animal = '🐇'
            elif item[3].days == sorted_platinum_hunters[-1][3].days:
                animal = '🐢'

        users += f"{item[0]}: {item[1]}{fire}{animal}\n"

    # format the game data as a string
    game_string = f"{game['title']} {game['title platform']}"

    # combine the game and user progress data into a final string
    final_string = f"{game_string}\n\n{users}"
    return final_string

# start search in db
@dp.message_handler(commands=["find"])
async def find(message):
    name = message.text.split()[1:]
    name = " ".join(name)
    result = find_game(name)
    if len(result) == 0:
        await bot.send_message(message.chat.id, "Game not found")
    elif len(result) == 1:
        game = result[0]
        answer = compose_answer(game)
        await bot.send_photo(message.chat.id, game['image'], answer)
    else:
        await bot.send_message(message.chat.id, "more than 1 game found")
        # make inline keyboard with found games and versions, send np_title_id to callback
        keyboard = types.InlineKeyboardMarkup()
        for game in result:
            keyboard.add(types.InlineKeyboardButton(text=game['title']+ ' ' + game['title platform'], callback_data=game['_id']))
        await bot.send_message(message.chat.id, "Choose version", reply_markup=keyboard)


# callback for inline keyboard
@dp.callback_query_handler(lambda c: True)
async def process_callback_button1(callback_query: types.CallbackQuery):
    # get game from db by np_title_id
    game = all_stats.find_one({"_id": callback_query.data})
    # if game not found
    if game is None:
        await bot.send_message(callback_query.message.chat.id, "Game not found")
        await bot.answer_callback_query(callback_query.id)
        return
    # if game found
    answer = compose_answer(game)
    await bot.send_photo(callback_query.message.chat.id, game['image'], answer)
    await bot.answer_callback_query(callback_query.id)




@dp.message_handler(commands=["test"])
async def test(message):
    # find number of records in collection all_stats
    res = all_stats.find()
    print(len(list(res)))



    res = all_stats.find_one()
    print(len(res))
    res = all_stats.find_one({"_id": "NPWR08997_00"})
    print(res)
    if res is None:
        await bot.send_message(message.chat.id, "Game not found")
    else:
        await bot.send_message(message.chat.id, "Game found")

@dp.message_handler(commands=["erase"])
async def erase(message):
    all_stats.delete_many({})
    users_collection.delete_many({})
    await bot.send_message(message.chat.id, "DB erased")



async def background_on_start() -> None:
    """background task which is created when bot starts"""

    while True:
        friends_check()
        await asyncio.sleep(350 + random.randint(1, 10))


async def on_bot_start_up(dispatcher: Dispatcher) -> None:
    """List of actions which should be done before bot start"""
    asyncio.create_task(background_on_start())  # creates background task


executor.start_polling(dp, on_startup=on_bot_start_up)
