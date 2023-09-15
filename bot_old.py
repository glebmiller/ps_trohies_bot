from aiogram import Bot, Dispatcher, executor, types
from psnawp.src.psnawp_api import psnawp
import pymongo
import re
import database
import os
import asyncio
import telegram
import random
import datetime
import logging
from subprocess import PIPE, Popen
from telegram import ParseMode
from time import sleep
from psnawp.src.psnawp_api.core.psnawp_exceptions import PSNAWPException 



def cmdline(command):
    process = Popen(args=command, stdout=PIPE, shell=True)
    return process.communicate()[0]


# Configure logging
logging.basicConfig(level=logging.INFO)

psnawp = psnawp.PSNAWP(database.getPSNToken())

# подключаемся к БД
client = pymongo.MongoClient("mongodb://localhost:27017")
db = client.PSNTrophies

# общая таблица без названий трофеев
collection = db.games
# таблица пользователей
users_collection = db.users

# таблица игр со списком трофеев с названиями
games_collection = db.games_with_trophies

CHATID = database.get_chat_id()

# смотрим, есть ли игра в БД
def game_in_db(game_id):
    query = {"_id": game_id}
    res = list(collection.find(query))
    if len(res) == 0:
        return False
    else:
        return True


def add_game_to_db(game_id, version, user_account_id):
    result = user_account_id.get_game_trophy_names(game_id, version)
    list_of_trophies = result["trophies"]
    values = {"_id": game_id, "trophies": list_of_trophies}
    games_collection.insert_one(values)

"""
def game_trophies(game_id, version, user_account_id):
    result = user_account_id.get_game_trophies(game_id, version)
    return result
"""

def game_trophies(game_id, version, user_account_id):
    #version = check_game_version(platform, game_id)
    try:
        result = user_account_id.get_game_trophies(game_id, version)
        return result
    
    except PSNAWPException as e:
        print(f"Error: {e}")
        return None


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
        "user complitage": [{login: progress}],
        "title platform": trophyTitlePlatform,
        "image": trophyTitleIconUrl,
    }
    collection.insert_one(values)


# добавляем данные пользователя в collection
def update_collection(login):
    user_account_id = psnawp.user(online_id=login)
    all_user_trophies = user_account_id.get_all_trophies()

    trophyTitles = all_user_trophies["trophyTitles"]
    # values = {}
    for game in trophyTitles:
        # если игры нет в бд, добавляем запись полностью
        if not game_in_db(game["npCommunicationId"]):
            add_game_to_collection(
                game["npCommunicationId"],
                game["trophyTitleName"],
                login,
                game["progress"],
                game["trophyTitlePlatform"],
                game["trophyTitleIconUrl"],
            )

        else:  # если игра есть, только добавляем пользователя в список
            _id = game["npCommunicationId"]
            progress = game["progress"]

            collection.update_one(
                {"_id": _id}, {"$push": {"user complitage": {login: progress}}}
            )

        # добавляем пользователя в users_collection, а его данные в  collection


def add_user(login):
    # complete_users_collection(login)
    users_collection.insert_one({"_id": login, "games": []})

    update_collection(login)


# поиск игры по ключу в бд collection
def find_game_by_id(game):
    query = {"_id": re.compile(game, re.IGNORECASE)}
    res = list(collection.find(query))
    return res


# поиск игры в БД по названию
def find_game(game):
    query = {"title": re.compile(game, re.IGNORECASE)}
    # result = []
    res = collection.find(query)
    return res


# токен телеграм бота Wisely
TOKEN = database.getBotToken()

telebot = telegram.Bot(token=TOKEN)

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)


# извлечь аргумент команды в виде строки
def extract_arg(arg):
    # s=''
    n = arg.split()[1:]
    s = " ".join(n)
    return s


# вовращает id игры по названию и платформе


def game_id_from_name(game_name, platform):
    query = {"title": game_name, "title platform": platform}
    tmp = list(collection.find(query))
    return tmp[0]["_id"]


# составляем ответ для /find game
def compose_answer(found_game):
    game_name = found_game[0]
    platform = found_game[2]

    tmp_list = []
    d = found_game[1]

    result = game_name + " " + platform + "\n"
    for line in d:
        for keys, vals in line.items():
            tmp_list.append((keys, vals))
    tmp_list = sorted(tmp_list, key=lambda x: x[1], reverse=True)
    print("tmp_list = ", tmp_list)
    game_id = game_id_from_name(game_name, platform)
    tmp_list = check_if_platinum(tmp_list, game_id, platform)
    for i in tmp_list:
        print(i)
        if i[2]:
            result += "\n" + i[0] + ": " + str(i[1]) + "\U0001F525"
            if i[5]:
                result += i[6]
        else:
            result += "\n" + i[0] + ": " + str(i[1])

    return result


def check_if_platinum(user_list, game_id, platform):
    today = datetime.datetime.today()
    temp_date = datetime.datetime.strptime("2950-03-28T8:13:11", "%Y-%m-%dT%H:%M:%S")
    tmp_list = []
    for i in user_list:
        # print(" i for i in user list", i)

        query = {"_id": i[0]}

        searched_game = {}
        all_user_games = list(users_collection.find(query))
        arr = all_user_games[0]["games"]
        # arrr = arr['games']
        for j in arr:
            for keys, vals in j.items():
                if keys == game_id:
                    searched_game = vals
                    break

        if searched_game == {}:
            print(i[0])
            user_account_id = psnawp.user(online_id=i[0])
            online_trophies = game_trophies(game_id, platform, user_account_id)
            searched_game = online_trophies

            users_collection.update_one(
                {"_id": i[0]},
                {"$push": {"games": {game_id: online_trophies}}},
                True,
                True,
            )

        plat = searched_game["trophies"]
        if plat[0]["trophyType"] == "platinum" and plat[0]["earned"]:
            print("platinum")
            earnedDateList = []
            for j in plat:
                # print(i)
                if j["earned"]:
                    earnedDateList.append(j["earnedDateTime"])

            earnedDateList = sorted(earnedDateList)
            firstTrophy = datetime.datetime.strptime(
                earnedDateList[-1], "%Y-%m-%dT%H:%M:%SZ"
            )
            lastTrophy = datetime.datetime.strptime(
                earnedDateList[0], "%Y-%m-%dT%H:%M:%SZ"
            )

            print(firstTrophy - lastTrophy)
            tmp_list.append((True, firstTrophy - lastTrophy, lastTrophy))
        else:
            tmp_list.append((False, temp_date - today, temp_date))

    for i in range(len(user_list)):
        user_list[i] = user_list[i] + tmp_list[i]

    user_list = sorted(user_list, key=lambda x: x[3])
    user_list[0] = user_list[0] + (True, "\U0001F407")

    for i in range(len(user_list) - 1, -1, -1):
        if user_list[i][2]:
            user_list[i] = user_list[i] + (True, "\U0001F422 ")
            break

    for i in range(len(user_list)):
        if len(user_list[i]) == 5:
            user_list[i] = user_list[i] + (False,)

    user_list = sorted(user_list, key=lambda x: x[0])
    user_list = list(sorted(user_list, key=lambda x: (x[1]), reverse=True))
    return user_list


# подготовить список из данных одной игры
def make_single_list(list_of_games):
    found_game = []
    temp_d = dict(list_of_games)
    for keys, vals in temp_d.items():
        if keys in ("image", "title", "user complitage", "title platform"):
            found_game.append(vals)
    return found_game


# ищет заданную игру
@dp.message_handler(commands=["find"])
async def find(message):
    game = extract_arg(message.text)
    list_of_games = list(find_game(game))
    if len(list_of_games) == 1:
        found_game = make_single_list(list_of_games[0])
        result = compose_answer(found_game)

        await bot.send_photo(message.chat.id, found_game[3], result)
    elif list_of_games == []:
        await bot.send_message(message.chat.id, f"Can not find {game}")
    else:
        if len(game) > 2:
            # print(list_of_games[0])
            games_array = []
            ids_array = []
            for lines in list_of_games:
                s = ""
                for keys, vals in lines.items():
                    if keys == "title" or keys == "title platform":
                        s += vals + " "
                    if keys == "_id":
                        ids_array.append(vals)
                s = s.strip()
                games_array.append(s)

            if len(games_array) == len(set(games_array)):

                buttons_list = dict(zip(games_array, ids_array))
                # print(buttons_list)

                markup = types.InlineKeyboardMarkup()

                for key, value in buttons_list.items():
                    markup.add(
                        types.InlineKeyboardButton(text=key, callback_data=value)
                    )

                await message.reply("Выбери игру", reply_markup=markup)

                # print(list_of_games[0]["_id"])
            else:
                buttons_list = [(i, j) for i, j in zip(games_array, ids_array)]
                markup = types.InlineKeyboardMarkup()
                for i in buttons_list:
                    markup.add(
                        types.InlineKeyboardButton(text=i[0], callback_data=i[1])
                    )
                await message.reply("Выбери игру", reply_markup=markup)


@dp.callback_query_handler()
async def button_reply(call: types.CallbackQuery):
    f = find_game_by_id(call.data)
    print(f"f = {f}")
    found_game = make_single_list(f[0])
    print(f"found_game = {found_game}")

    result = compose_answer(found_game)
    print(f"resul = {result}")
    await bot.answer_callback_query(call.id)
    await bot.send_photo(call.message.chat.id, found_game[3], result)
    await call.answer()


# проверяет, если ли user в users_collection
def check_if_user_in_db(login):
    query = {"_id": login}
    res = list(users_collection.find(query))
    if len(res) == 0:
        return False
    else:
        return True


# добавляем пользователя если он в друзьях и еще не добавлен
@dp.message_handler(commands=["add"])
async def add(message):
    login = extract_arg(message.text)

    user_account_id = psnawp.user(online_id=login)
    friend = user_account_id.friendship()
    if friend["friendRelation"] == "friend" and not check_if_user_in_db(login):
        await bot.send_message(message.chat.id, f"adding {login} to DB")
        add_user(login)
    elif friend["friendRelation"] == "friend" and check_if_user_in_db(login):
        await bot.send_message(message.chat.id, f"{login} alredy in DB")
    else:
        await bot.send_message(
            message.chat.id, "Become friends with MillerUSACC first!"
        )


# обновляет game complitage в таблице games
def game_complitage_update(login, game_id):
    user_account_id = psnawp.user(online_id=login)
    all_trophies = user_account_id.get_all_trophies()

    trophyTitles = all_trophies["trophyTitles"]

    for game in trophyTitles:
        if game["npCommunicationId"] == game_id:
            progress = game["progress"]

            collection.update_one(
                {"_id": game_id}, {"$pull": {"user complitage": {login: {"$exists": True}}}}
            )
            collection.update_one(
                {"_id": game_id}, {"$push": {"user complitage": {login: progress}}}
            )


# отбирает трофеи для печати
def print_new_trophies(difference, game_id, rates, trophy_type):
    query = {"_id": game_id}
    res = list(games_collection.find(query))
    trophies_to_print = []
    tmp_val = res[0]["trophies"]
    if isinstance(tmp_val, list):
        list_to_check = res[0]["trophies"]
    else:
        list_to_check = res[0]["trophies"]["trophies"]
    for game in list_to_check:
        temp_list = []
        if game["trophyId"] in difference[::-1]:
            st = game["trophyDetail"]
            st = st.replace('"', "'")
            temp_list.append(game["trophyName"])
            temp_list.append(st)
            temp_list.append(game["trophyType"])
            temp_list.append(game["trophyIconUrl"])
            temp_list.append(rates[difference.index(game["trophyId"])])
            temp_list.append(trophy_type[difference.index(game["trophyId"])])

            trophies_to_print.append(temp_list)
    return trophies_to_print


def trophy_received(saved_game, online_trophies):

    old_list = saved_game["trophies"]
    new_list = online_trophies["trophies"]
    difference = []
    rates = []
    trophy_type = []
    today = datetime.date.today()
    for trophy_id in range(len(old_list)):

        if old_list[trophy_id]["earned"] != new_list[trophy_id]["earned"]:

            print(new_list[trophy_id]["earnedDateTime"][:10])
            date_earned = new_list[trophy_id]["earnedDateTime"][:10]
            date_earned = datetime.datetime.strptime(date_earned, "%Y-%m-%d").date()
            bb = int(str(today)[-2:])
            aa = int(str(date_earned)[-2:])
            if bb - aa <= 2:
                rate = new_list[trophy_id]["trophyEarnedRate"]
                difference.append(new_list[trophy_id]["trophyId"])
                rates.append(rate)
                trophy_type.append(new_list[trophy_id]["trophyType"])
    return difference, rates, trophy_type

"""
def new_dlc(game_id, bot_friends, online_trophies, platform):
    # внести изменения в users_collection
    # надо обновить таблицу игры с названиями трофеями

    query = {"_id": game_id}
    games_collection.delete_one(query)

    values = {"_id": game_id, "trophies": online_trophies}
    games_collection.insert_one(values)

    # надо обновить таблицу со статистикой
    # update_collection(user_name)

    for friend in bot_friends:

        user_name = get_user_name(friend)
        game_complitage_update(user_name, game_id)

    check_new_trophies(game_id, user_name, platform, bot_friends)
"""

def new_dlc(game_id, bot_friends, online_trophies, platform):
    """
    Adds a new DLC to the game with the given ID, updates users' game completion stats and checks for new trophies

    :param game_id: ID of the game
    :param bot_friends: list of bot friends
    :param online_trophies: list of trophies for the new DLC
    :param platform: platform of the game
    """

    # Update the trophies for the game
    games_collection.update_one({"_id": game_id}, {"$set": {"trophies": online_trophies}})

    # Update users' completion stats for the game
    for friend in bot_friends:
        user_name = get_user_name(friend)
        game_complitage_update(user_name, game_id)

        # Check for new trophies and send notifications if necessary
        new_trophies = check_new_trophies(game_id, user_name, platform, online_trophies)


# проверяет, получил ли пользоваель новые трофеи
"""
def check_new_trophies(game_id, user_name, platform, bot_friends):
    print(game_id)
    user_account_id = psnawp.user(online_id=user_name)
    query = {"_id": user_name}
    print("user_name = ", user_name)
    res = list(users_collection.find(query))
    #print('res[0]["games"] = ', res[0]["games"])
    #sleep(300)
    if len(res) > 0:
        for i in res[0]["games"]:
            try:
                saved_game = i[game_id]
            except:
                pass

        online_trophies = game_trophies(game_id, platform, user_account_id)

        if saved_game["lastUpdatedDateTime"] != online_trophies["lastUpdatedDateTime"]:

            # if got new trophy
            if saved_game["totalItemCount"] == online_trophies["totalItemCount"]:

                difference, rates, trophy_type = trophy_received(
                    saved_game,
                    online_trophies,
                )

                # внести изменения в users_collection
                users_collection.update_one(
                    {"_id": user_name},
                    {"$pull": {"games": {game_id: saved_game}}},
                    True,
                    False,
                )
                users_collection.update_one(
                    {"_id": user_name},
                    {"$push": {"games": {game_id: online_trophies}}},
                    True,
                    True,
                )

                trophies_to_print = print_new_trophies(
                    difference, game_id, rates, trophy_type
                )

                # обновляет game complitage в таблице games
                game_complitage_update(user_name, game_id)
                f = find_game_by_id(game_id)

                found_game = make_single_list(f[0])
                send_trophies_to_chat(
                    trophies_to_print, user_name, found_game[0], platform
                )
            else:
                os.system(f"echo 'вышло DLC {game_id}' >> log.log")
                new_dlc(game_id, bot_friends, online_trophies, platform)
"""

def check_new_trophies(game_id, user_name, platform, bot_friends):
    print(game_id)
    
    try:
        user_account_id = psnawp.user(online_id=user_name)
        
    except PSNAWPException:
        print(f'User "{user_name}" not found on PSNAWP.')
        return

    query = {"_id": user_name}
    print("user_name = ", user_name)
    res = list(users_collection.find(query))
    
    if len(res) > 0:
        saved_game = None  # initialize saved_game to None
        
        for i in res[0]["games"]:
            try:
                saved_game = i[game_id]
            except:
                pass

        online_trophies = game_trophies(game_id, platform, user_account_id)

        if online_trophies is None:
            print(f"Error retrieving trophies for game {game_id} for user {user_name}.")
            return

        if saved_game is not None and saved_game["lastUpdatedDateTime"] != online_trophies["lastUpdatedDateTime"]:


            # if got new trophy
            if saved_game["totalItemCount"] == online_trophies["totalItemCount"]:

                difference, rates, trophy_type = trophy_received(
                    saved_game,
                    online_trophies,
                )

                # внести изменения в users_collection
                users_collection.update_one(
                    {"_id": user_name},
                    {"$pull": {"games": {game_id: saved_game}}},
                    True,
                    False,
                )
                users_collection.update_one(
                    {"_id": user_name},
                    {"$push": {"games": {game_id: online_trophies}}},
                    True,
                    True,
                )

                trophies_to_print = print_new_trophies(
                    difference, game_id, rates, trophy_type
                )

                # обновляет game complitage в таблице games
                game_complitage_update(user_name, game_id)
                f = find_game_by_id(game_id)

                found_game = make_single_list(f[0])
                send_trophies_to_chat(
                    trophies_to_print, user_name, found_game[0], platform
                )

            else:
                os.system(f"echo 'вышло DLC {game_id}' >> log.log")
                new_dlc(game_id, bot_friends, online_trophies, platform)

def make_html(trophies_to_print, user_name, gamename):
    print(trophies_to_print)
    with open("blank_page.html", "r") as file:
        content = file.read()
        content = content.replace("insert_title", trophies_to_print[0])
        content = content.replace(
            "insert_description",
            trophies_to_print[1]
            + "\n"
            + trophies_to_print[-2]
            + " - "
            + trophies_to_print[-1],
        )
        content = content.replace("insert_image", trophies_to_print[3])
        content = content.replace("insert_username", user_name)
        content = content.replace("insert_gamename", gamename)
    today = str(datetime.datetime.now())
    today = today.replace(" ", "")
    url = today + ".html"
    with open(url, "w") as file:
        file.write(content)
        file.close()
    return url


def send_trophies_to_chat(trophies_to_print, user_name, game_name, platform):
    for trophy in trophies_to_print:
        url = make_html(trophy, user_name, game_name)
        os.system(f"sudo cp {url} /var/www/tutorial/{url}")
        result = f"{user_name} -<a href='http://ec2-3-82-93-156.compute-1.amazonaws.com/{url}'> {game_name}</a> - {platform.upper()}"
        telebot.sendMessage(CHATID, result, parse_mode=ParseMode.HTML)
        os.remove(url)
        if trophy[2] == "platinum":
            telebot.send_sticker(
                CHATID,
                sticker="CAACAgIAAxkBAAEV9xliz9cW_7inof3UGYHVLF3AbJuy_QACTwsAAkKvaQABE3jwX_D6RZYpBA",
            )


def get_user_name(friend):
    user_name = str(friend)
    user_name = user_name[11:]
    user_name = user_name[: user_name.find(" ")]
    return user_name


def friends_check():

    bot_pid = os.getpid()
    os.system(f"touch {bot_pid}.txt")

    psn_client = psnawp.me()
    bot_friends = psn_client.friends_list()

    for friend in bot_friends:

        s = str(friend)
        parts = s.split(" ")
        #print(parts)
        user_name = parts[2]

        user_id = psnawp.user(online_id=user_name)
        print("user_name: ", user_name)

        
        all_user_games = user_id.get_all_trophies()
        last_user_games = all_user_games["trophyTitles"][:2]
        print(last_user_games)
        
        # находит game_id по названию и добавляет в collection, если там игры не было
        
        
        for game in last_user_games:
            game_id = game["npCommunicationId"]
            platform = game["trophyTitlePlatform"]
            #print(f'game title = {game["trophyTitleName"]}')
            check_game_in_collection(game_id, user_id, user_name, last_user_games)
            if game_id != "":
                # проверим, есть ли информация о трофеях игры
                check_game_in_games_db(game_id, platform, user_id)

                # проверим, есть ли информация о трофеях игры в таблице пользователи с названиями трофеев
                # при первом запуске игры занесет информацию о ней в таблицу
                check_game_in_users_db(game_id, platform, user_name, user_id)

                # проверяем, появились ли новые трофеи

                check_new_trophies(game_id, user_name, platform, bot_friends)
            sleep(1)


def check_game_in_users_db(game_id, platform, user_name, user_id):
    result = game_trophies(game_id, platform, user_id)

    tmpstr = "games." + game_id
    query = {
        "$and": [
            {"_id": re.compile(user_name, re.IGNORECASE)},
            {tmpstr: {"$exists": True}},
        ]
    }
    res = list(users_collection.find(query))
    if len(res) == 0:
        users_collection.update_one(
            {"_id": user_name}, {"$push": {"games": {game_id: result}}}
        )


def check_game_in_games_db(game_id, platform, user_id):
    query = {"_id": game_id}
    res = list(games_collection.find(query))
    print(game_id)
    # print(res)
    if len(res) == 0:
        add_game_to_db(game_id, platform, user_id)


# check and add game to collection
def check_game_in_collection(game_id, user_id, login, last_user_games):
    try:
        query = {"_id": game_id}
        res = list(collection.find(query))
        if res == []:

            # впервые кто то запустил эту игру и ее надо добавить в collection
            print(" впервые кто то запустил эту игру и ее надо добавить в collection")
            for game in last_user_games:
                if game["npCommunicationId"] == game_id:
                    print("нашли игру")
                    telebot.sendMessage(
                        chat_id=CHATID,
                        text=f"{login} первый, кто играет в {game['trophyTitleName']} - {game['trophyTitlePlatform']}",
                    )

                    add_game_to_collection(
                        game["npCommunicationId"],
                        game["trophyTitleName"],
                        login,
                        game["progress"],
                        game["trophyTitlePlatform"],
                        game["trophyTitleIconUrl"],
                    )
                    print(game["npCommunicationId"])

    except:
        os.system(
            f"echo 'something went wrong when adding {game_id} to collection' >> log.log"
        )


async def start_friends_check(interval, periodic_function):
    while True:
        await asyncio.gather(
            asyncio.sleep(interval),
            periodic_function(),
        )


async def background_on_start() -> None:
    """background task which is created when bot starts"""

    while True:

        friends_check()
        await asyncio.sleep(350 + random.randint(1, 10))


async def on_bot_start_up(dispatcher: Dispatcher) -> None:
    """List of actions which should be done before bot start"""
    asyncio.create_task(background_on_start())  # creates background task


executor.start_polling(dp, on_startup=on_bot_start_up)
