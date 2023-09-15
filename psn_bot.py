
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
import random
import logging
from subprocess import PIPE, Popen
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import csv
from typing import List
from itertools import groupby, chain


class TrophyType(Enum):
    BRONZE = 'bronze'
    SILVER = 'silver'
    GOLD = 'gold',
    PLATINUM = 'platinum'


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

AMAZON_URL = database.get_amazon_url()

# Setting up telegram bot Wisely
TOKEN = database.getBotToken()
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# sending formatted html to telegram
async def send_to_chat(url, game_name, platform, user_name, platinum=False):
    os.system(f"sudo cp {url} /var/www/tutorial/{url}")
    result = f"{user_name} -<a href='{AMAZON_URL}{url}'> {game_name}</a> - {platform.upper()}"
    print(result)
    await bot.send_message(CHATID, result, parse_mode=types.ParseMode.HTML)
    os.remove(url)
    if platinum:
        await bot.send_sticker(CHATID, sticker="CAACAgIAAxkBAAEV9xliz9cW_7inof3UGYHVLF3AbJuy_QACTwsAAkKvaQABE3jwX_D6RZYpBA")

   


# making html from trophy data
async def make_html(trophies):
    print(trophies)
    
    for trophy in trophies[3:]:
        plat = False
        if trophy['trophytype'] == "PLATINUM":
            plat = True

        with open("blank_page.html", "r") as file:
            content = file.read()
            content = content.replace("insert_title", trophy['trophy_name'])
            content = content.replace(
                "insert_description",
                trophy['description']
                + "\n"
                + trophy['percentage']
                + " - "
                + trophy['trophytype'],
            )
            content = content.replace("insert_image", trophy['icon'])
            content = content.replace("insert_username", trophies[0])
            content = content.replace("insert_gamename", trophies[1])
        today = str(datetime.now())
        today = today.replace(" ", "")
        url = today + ".html"
        with open(url, "w") as file:
            file.write(content)
            file.close()
        await send_to_chat(url, game_name=trophies[1], platform=trophies[2], user_name=trophies[0], platinum=plat)
    #update last user update time in users collection
    users_collection.update_one({"_id": trophies[0]}, {"$set": {"date_added": datetime.now(timezone.utc)}})

async def friends_check():
    # get list of friends
    psn_client = psnawp.me()
    bot_friends = psn_client.friends_list()

    for friend in bot_friends:
        print(friend.online_id)
        #if friend.online_id in ['gorcheque', 'gleb_miller', 'GingerKrololo']:

        #check when friend got last trophy recorded in DB
        #last_trophy_date = all_stats.find_one({"_id": friend.online_id}, sort=[("date", -1)])
        # get date_added from users collection
        last_trophy_date = users_collection.find_one({"_id": friend.online_id})['date_added']
        print(last_trophy_date)
        



        #get new trophies since last trophy recorded in DB
        client = psnawp.user(online_id=friend.online_id)
        for trophy_title in client.trophy_titles(limit=3):
            print(trophy_title)
            last_updated_date_time = trophy_title.last_updated_date_time
            #compare last trophy date in DB with last trophy date in PSN
            # fix can't compare offset-naive and offset-aware datetimes error
            last_updated_date_time = last_updated_date_time.replace(tzinfo=None)

            if last_trophy_date < last_updated_date_time:
                print("new trophy")
                #get game name
                game_name = trophy_title.title_name
                #get game id
                np_communication_id = trophy_title.np_communication_id
                #get platform
                platform = next(iter(trophy_title.title_platform)).value
                #get progress
                progress = trophy_title.progress
                #get icon url
                icon_url = trophy_title.title_icon_url
                # if game not in DB
                if all_stats.find_one({"_id": np_communication_id}) is None:
                    add_game_to_collection(np_communication_id, game_name, friend.online_id, progress, platform, icon_url)
                    print(f"{game_name} added to DB")
                    sleep(0.1)
                else:
                    update_game_in_collection(np_communication_id, friend.online_id, progress)
                    print(f"{game_name} updated in DB")
                    sleep(0.1)

                game_trophies = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=False)
                #make dict with this keys username, gamename, platform, trophyname, description, percentage, trophy type, icon
                #received_trophy = {'trophy_id': None, 'user_name': friend.online_id, 'game_name': game_name, 'platform': platform, 'trophy_name': '', 'description': '', 'percentage': '', 'trophytype': '', 'icon': ''}

                received_trophies = [friend.online_id, game_name, platform]

                for single_trophy in game_trophies:
                    #print(single_trophy)
                    if single_trophy.earned_date_time == None:
                        # go to next iteration if trophy is not earned
                        continue

                    # fix TypeError: can't compare offset-naive and offset-aware datetimes error
                    #single_trophy.earned_date_time = single_trophy.earned_date_time.replace(tzinfo=None)
                    # fix     raise FrozenInstanceError()attr.exceptions.FrozenInstanceError 
                    #print("single_trophy.earned_date_time", type(single_trophy.earned_date_time))
                    #print("last_trophy_date", type(last_trophy_date))
                    new_trophy_date = single_trophy.earned_date_time.replace(tzinfo=None)
                    last_trophy_date = last_trophy_date.replace(tzinfo=None)

                    if new_trophy_date > last_trophy_date:
                        # make a dict of new trophies with trophy_id as key, and dict with all info as value. needed info is percentage, trophytype, icon, description
                        received_trophy = {}
                        received_trophy['trophy_id'] = single_trophy.trophy_id
                        received_trophy['percentage'] = single_trophy.trophy_earn_rate
                        received_trophy['trophytype'] = single_trophy.trophy_type.name
                        received_trophy['icon'] = single_trophy.trophy_icon_url
                        received_trophy['description'] = single_trophy.trophy_detail
                        received_trophy['trophy_name'] = single_trophy.trophy_name
                        received_trophies.append(received_trophy)
                
                # get title_id the same way like it's done in functon check_platinum
                title_ids = get_title_ids_by_name(game_name)
                #print(title_ids)
                title_id = None
                for try_title_id in title_ids:
                    try:
                        for trophy_title_info in client.trophy_titles_for_title(title_ids=[try_title_id]):
                            print("id found")
                            title_id = try_title_id
                            break
                    except:
                        print("exeption No trophies for this game")
                        print()
                    if title_id is not None:
                        break
                print("title_id", title_id)
                if title_id is not None:
                    all_trophy_names = psnawp.game_title(title_id=title_id, account_id=friend.account_id, np_communication_id=np_communication_id)
                    #for trophy in zxc:
                    #print(zall_trophy_namesxc.trophies(platform="PS5"))
                    for trophy in all_trophy_names.trophies(platform=platform):
                        #print(received_trophies)
                        for received_trophy in received_trophies[3:]:
                            
                            if received_trophy['trophy_id'] == trophy.trophy_id:
                                received_trophy['trophy_name'] = trophy.trophy_name
                                received_trophy['description'] = trophy.trophy_detail
                                received_trophy['icon'] = trophy.trophy_icon_url
                                break
                    
                    #send message to telegram
                    await make_html(received_trophies)
            else:
                print("no new trophies")
                print()
                break



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
    #replace user progress with new one
    # replace user progress with new one
    
    all_stats.update_one(
                {"_id": npCommunicationId}, {"$pull": {"user progress": {login: {"$exists": True}}}}
            )
    all_stats.update_one(
        {"_id": npCommunicationId}, {"$push": {"user progress": {login: progress}}}
    )
    """

    all_stats.update_one(
    {"_id": npCommunicationId},
    [
        {
            "$set": {
                f"user progress.$[elem].{login}": {
                    "$cond": {
                        "if": {"$eq": [{"$ifNull": [f"$user progress.$[elem].{login}", None]}, None]},
                        "then": progress,
                        "else": {"$mergeObjects": [f"$user progress.$[elem].{login}", progress]}
                    }
                }
            }
        },
        {
            "$pull": {
                "user progress": {
                    f"$or": [
                        {login: {"$exists": False}},
                        {login: None},
                        {login: {}}
                    ]
                }
            }
        }
    ],
    array_filters=[{"elem.login": {"$eq": login}}]
    )
    """

    


# adds percentage stats to table for 1 user
def add_all_user_games(login):

    client = psnawp.user(online_id=login)
    for trophy_title in client.trophy_titles(limit=None):
        game_name = trophy_title.title_name
        np_communication_id = trophy_title.np_communication_id
        platform = next(iter(trophy_title.title_platform)).value
        progress = trophy_title.progress
        icon_url = trophy_title.title_icon_url
        # if game not in DB
        if all_stats.find_one({"_id": np_communication_id}) is None:
            add_game_to_collection(np_communication_id, game_name, login, progress, platform, icon_url)
            print(f"{game_name} added to DB")
            sleep(0.1)
        else:
            update_game_in_collection(np_communication_id, login, progress)
            print(f"{game_name} updated in DB")
            sleep(0.1)

        # if game in DB
        # update_game_in_collection(np_communication_id, game_name, login, progress, platform, icon_url)
        sleep(0.5)
    print(f"all {login}'s games added to DB")
        

"""# add user to DB
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

"""

# add user to DB
@dp.message_handler(commands=["add"])
async def add(message):
    try:
        login = message.text.split()[1]
        user_account_id = psnawp.user(online_id=login)
        friend = user_account_id.friendship()
        friend_relation = friend["friendRelation"]

        if friend_relation == "friend":
            if not check_if_user_in_db(login):
                await bot.send_message(message.chat.id, f"Adding {login} to DB")
                add_user(login)
                add_all_user_games(login)
            else:
                await bot.send_message(message.chat.id, f"{login} already in DB")
        else:
            await bot.send_message(message.chat.id, "Become friends with MillerUSACC first!")
    
    except Exception as e:
        print(f"Error: {e}")
        await bot.send_message(message.chat.id, "Something went wrong")


# looking for a game in the database
def find_game(game):
    query = {"title": re.compile(game, re.IGNORECASE)}
    # result = []
    res = all_stats.find(query)
    return list(res)
"""
# write a function to find a game in the database by np_communication_id
def find_game_by_id(np_communication_id):
    query = {"_id": np_communication_id}
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

# get title ids by name from csv file
def get_title_ids_by_name(name: str) -> List[str]:
    # in name remove ® and ™
    name = name.replace('®', '')
    name = name.replace('™', '')
    #remove spaces and new lines from name
    name = name.strip()


    title_ids = []
    with open('/home/ubuntu/Projects/Python/psn_2.0/psnawp/PlayStation-Titles/All_Titles.tsv', newline='') as tsvfile:
        reader = csv.DictReader(tsvfile, delimiter='\t')
        for row in reader:
            # remove ® and ™ from row['name']
            row['name'] = row['name'].replace('®', '')
            row['name'] = row['name'].replace('™', '')

            if name.lower() in row['name'].lower():
                title_ids.append(row['titleId'])
    return title_ids


#check if user has platinum trophy in game
def check_platinum(game, np_communication_id, platform):
    result = []
    name = game['title']
    title_id = None
    title_ids = get_title_ids_by_name(name)
    print(title_ids)
    # reverse title_ids list
    title_ids.reverse()

    for users in game['user progress']:
        for user_name in users.keys():
            print("user_name =", user_name)
            print()
            user_data = [user_name]
            client = psnawp.user(online_id=user_name)
            if not title_id:
                for try_title_id in title_ids:
                    #print("title_id =", title_id)
                    #print()
                    try:
                        for trophy_title_info in client.trophy_titles_for_title(title_ids=[try_title_id]):
                            print("id found")
                            print(try_title_id)
                            title_id = try_title_id
                            break
                    except:
                        print("exeption No trophies for this game")
                        print()
                    if title_id is not None:
                        break


            print(title_id)
                
            
            for trophy_title_info in client.trophy_titles_for_title(title_ids=[title_id]):
                user_data.append(trophy_title_info.progress)
                print("trophy_title_info =", trophy_title_info)
                print()

                if trophy_title_info.earned_trophies['platinum'] == 1:
                    user_data.append(1)
                    print("Platinum")
                else:
                    user_data.append(0)
                    user_data.append(0)
                    print("No Platinum")
                if user_data[2] == 1:
                    print(np_communication_id)
                    print(platform)
                    try:
                        game_trophies = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=False)
                        time_spent = []
                        for single_trophy in game_trophies:
                            time_spent.append(single_trophy.earned_date_time)
                        #delete None values
                        time_spent = [x for x in time_spent if x is not None]
                        time_spent = sorted(time_spent)
                        #calculate delta of time between first and last trophy
                        delta = time_spent[-1] - time_spent[0]
                        user_data.append(delta)
                    except:
                        pass
                        
        result.append(user_data)
    return result

"""
#check if user has platinum trophy in game
def check_platinum(game):
    logging.debug("Getting title IDs for game: %s", game["title"])

    title_ids = get_title_ids_by_name(game["title"])
    print(title_ids)
    result = []
    title_id = None
    for user_data in game["user progress"]:
        user_name = next(iter(user_data))
        logging.debug("Retrieving data for user: %s", user_name)

        client = psnawp.user(online_id=user_name)
        print(user_name)
        #print(client)

        # Find trophy title with matching ID
        if not title_id:       
            for try_title_id in title_ids:
                try:
                    for trophy_title_info in client.trophy_titles_for_title(title_ids=[try_title_id]):
                        #print(trophy_title_info.title_name)
                        #print(game['title'])
                        if trophy_title_info.title_name == game['title']:
                            #print("id found")
                            #print(try_title_id)
                            title_id = try_title_id
                except:
                    logging.debug("No trophies for title ID: %s", try_title_id)
                    #print("exeption No trophies for this game123123123")

                if title_id:
                    logging.debug("Title ID found: %s", title_id)
                    break

            if not title_id:
                logging.debug("No matching title ID found for user: %s, game: %s", user_name, game["title"])
                print("exeption No trophies for this game")
                continue
            

        user_progress = [user_name]
        #print(user_progress)
        try:
            trophy_title_info = next(iter(client.trophy_titles_for_title(title_ids=[title_id])))
            #trophy_title_info = client.trophy_titles_for_title(title_ids=[title_id])[0]
        except:
            logging.debug("No trophy progress for title ID: %s", title_id)
            continue

        user_progress.append(trophy_title_info.progress)
        #print(user_progress)

        np_communication_id = trophy_title_info.np_communication_id
        game_platform = next(iter(trophy_title_info.title_platform)).value

        platinum_trophy = trophy_title_info.earned_trophies["platinum"]
        user_progress.append(1 if platinum_trophy else 0)
        #print(user_progress)
        platforms = ["PS3", "PS4", "PS5", "PS Vita", "UNKNOWN"]
        if platinum_trophy:
            try:
                game_trophies = client.trophies(np_communication_id=np_communication_id, platform=game_platform, include_metadata=False)
                trophy_dates = [t.earned_date_time for t in game_trophies if t.earned_date_time]
                if trophy_dates:
                    user_progress.append(max(trophy_dates) - min(trophy_dates))
                else:
                    logging.debug("No trophy dates found for user: %s, game: %s", user_name, game["title"])
            except:
                    



                for platform in platforms:
                    #make try except expression for each platform, stop when no exception
                    try:
                        game_trophies = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=False)
                        trophy_dates = [t.earned_date_time for t in game_trophies if t.earned_date_time]
                        if trophy_dates:
                            user_progress.append(max(trophy_dates) - min(trophy_dates))
                        else:
                            logging.debug("No trophy dates found for user: %s, game: %s", user_name, game["title"])
                        break
                    except:
                        print("No trophies for this game")
                    print()
            
                
            #print(user_progress)

            #game_trophies = client.trophies(np_communication_id=np_communication_id, platform="PS5", include_metadata=False)
            
        else:
            user_progress += [0, None]
        #print(user_progress)
        result.append(user_progress)

    return result

"""
def make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters):
    str_with_progress_and_emoji = ''
    for item in sorted_list:
        fire = ''
        animal = ''
        if item[2] == 1:
            fire = '🏆'
            if item[3].days == sorted_platinum_hunters[0][3].days:
                animal = '🐇'
            elif item[3].days == sorted_platinum_hunters[-1][3].days:
                animal = '🐢'

        str_with_progress_and_emoji += f"{item[0]}: {item[1]}{fire}{animal}\n"
    return str_with_progress_and_emoji


# make string from completage document
def compose_answer(game):
    print("game =", game)
    trophy_list = check_platinum(game, game['_id'], game['title platform'])
    if trophy_list is None:
        return None
    print("trophy_list =", trophy_list)
    platinum_hunters = []
    other_users = []
    for item in trophy_list:
        #print(item)
        if item[2] == 1:
            platinum_hunters.append(item)
        else:
            other_users.append(item)

    print("platinum_hunters =", platinum_hunters)
    print("other_users =", other_users)


    sorted_platinum_hunters = sorted(platinum_hunters, key=lambda x: x[3].days)
    sorted_other_users = sorted(other_users, key=lambda x: x[1], reverse=True)

    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = ''
    users = make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters)
    """
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
    """
    # format the game data as a string
    #game_string = f"{game['title']} {game['title platform']}"

    # combine the game and user progress data into a final string
    #final_string = f"{game_string}\n\n{users}"
    #return final_string
    print(users)
    return  {"title": game['title'], "platform": [game['title platform']], "users": users, 'sorted_platinum_hunters': sorted_platinum_hunters, 'sorted_other_users': sorted_other_users}



def compose_answer_group(games_ids: list = []):
    print("games_ids =", games_ids)
    group_result = []
    for game_id in games_ids:
            
        query = {"_id": game_id}
        # result = []
        res = all_stats.find(query)
        if res is not None:
            #print(compose_answer(res[0]))
            group_result.append(compose_answer(res[0]))

    print("group_result = 1 = ", group_result)
    for item in group_result:
        platform = item['platform'][0]
        for player in item['sorted_platinum_hunters']:
            player[0] = platform + ' ' + player[0]
        for player in item['sorted_other_users']:
            player[0] = platform + ' ' + player[0]

    print("group_result = 2 = ", group_result)

    # Group the dictionaries with the same title and platform
    grouped_records = []
    for key, group in groupby(group_result, key=lambda x: x['title']):
        dicts = list(group)
        combined_dict = {
            'title': key,
           # 'platform': list(chain(*[d['platform'] for d in dicts])),
            'platform': list(chain(*[d['platform'] for d in dicts])),
            'sorted_platinum_hunters': list(chain(*[d['sorted_platinum_hunters'] for d in dicts])),
            'sorted_other_users': list(chain(*[d['sorted_other_users'] for d in dicts])),
        }
        grouped_records.append(combined_dict)
        
    group_result = grouped_records

    print("group_result =", group_result)

    # Sort the grouped records by the time it took the fastest platinum hunter to get the platinum
    sorted_platinum_hunters = group_result[0]['sorted_platinum_hunters']
    sorted_other_users = group_result[0]['sorted_other_users']
    sorted_platinum_hunters = sorted(sorted_platinum_hunters, key=lambda x: x[3].days)
    sorted_other_users = sorted(sorted_other_users, key=lambda x: x[1], reverse=True)

    print("sorted_platinum_hunters =", sorted_platinum_hunters)
    print("sorted_other_users =", sorted_other_users)


    sorted_list = sorted_platinum_hunters + sorted_other_users

    users = ''
    users = make_str_with_progress_and_emoji(sorted_list, sorted_platinum_hunters)
    print(users)
    return  {"title": group_result[0]['title'], "platform": group_result[0]['platform'], "users": users, 'sorted_platinum_hunters': sorted_platinum_hunters, 'sorted_other_users': sorted_other_users}



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
        games_dict = compose_answer(game)
        answer = f"{games_dict['title']} {games_dict['platform'][0]}\n\n\n{games_dict['users']}"
        print(answer)
        print(games_dict)
        if answer is None:
            await bot.send_message(message.chat.id, "No trophies for this game")
        else:
            await bot.send_photo(message.chat.id, game['image'], answer)
    else:
        #await bot.send_message(message.chat.id, "more than 1 game found")
        # make inline keyboard with found games and versions, send np_communication_id to callback

        #check if result has multiple versions of the same game with different platforms
        # if yes, make lists of games with the same title and different platform and _id
        #print(result)
        games_by_title = {}
        for game in result:
            title = game['title']
            platform = game['title platform']
            game_id = game['_id']
            progress = game['user progress']
            image = game['image']
            
            if title not in games_by_title:
                games_by_title[title] = {
                    'title': title,
                    'platform': [platform],
                    'ids': [game_id],
                    'progress': progress,
                    'image': image
                }
            else:
                games_by_title[title]['ids'].append(game_id)
                games_by_title[title]['platform'].append(platform)
                games_by_title[title]['progress'] += progress

        games_list_grouped = list(games_by_title.values())
        #print(games_list_grouped)
        for game in games_list_grouped:
            print(game)
            print()

        # if games_list_grouped has only one game, send it
        if len(games_list_grouped) == 1:
            game = games_list_grouped[0]
            games_dict = compose_answer_group(game['ids'])
            #games_dict = compose_answer(game)
            answer = f"{games_dict['title']} {' '.join(games_dict['platform'])}\n\n\n{games_dict['users']}"
            #!!!!compose_answer_group
            if answer is None:
                await bot.send_message(message.chat.id, "No trophies for this game")
            else:
                await bot.send_photo(message.chat.id, game['image'], answer)
            return
        else:
            # if games_list_grouped has more than one game, send inline keyboard
            keyboard = types.InlineKeyboardMarkup()
            for game in games_list_grouped:
                game_id_str = ','.join(game['ids'])
                keyboard.add(types.InlineKeyboardButton(text=game['title'], callback_data=game_id_str))
            await bot.send_message(message.chat.id, "Choose game:", reply_markup=keyboard)
            return 
        
       


# callback for inline keyboard
@dp.callback_query_handler(lambda c: True)
async def process_callback_button1(callback_query: types.CallbackQuery):
    print('callback_query.data = ', callback_query.data)
    list_of_ids = callback_query.data.split(',')

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
        
        await bot.send_photo(callback_query.message.chat.id, game['image'], answer)
        await bot.answer_callback_query(callback_query.id)
        return
    else:
        game = all_stats.find_one({"_id": list_of_ids[0]})
        
        #print(callback_query.data)
        games_dict = compose_answer_group(list_of_ids)
        #games_dict = compose_answer(game)
        answer = f"{games_dict['title']} {' '.join(games_dict['platform'])}\n\n\n{games_dict['users']}"
        if answer is None:
            await bot.send_message(callback_query.message.chat.id, "No trophies for this game")
            await bot.answer_callback_query(callback_query.id)
        else:
            await bot.send_photo(callback_query.message.chat.id, game['image'], answer)
            await bot.answer_callback_query(callback_query.id)
        return


@dp.message_handler(commands=["test"])
async def test(message):
    """
    np_communication_id = "NPWR21586_00"
                #get platform
    platform = "PS5"
    client = psnawp.user(online_id="gleb_miller")
    title_id = ''
    get_trophy_summary_for_title = client.trophies.trophy_titles.get_trophy_summary_for_title(np_communication_id=np_communication_id, platform=platform)
    game_trophies = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=False)
    get_trophy_summary_for_title = psnawp.trophy_titles.get_trophy_summary_for_title(np_communication_id=np_communication_id, platform=platform)
     
    for single_trophy in game_trophies:
        print(single_trophy)
        print(single_trophy.earned_date_time)
        #print(single_trophy.trophy_title_name)
        #print(single_trophy.trophy_title_detail)
        print(single_trophy.trophy_icon_url)
        print(single_trophy.earned)
        #print(single_trophy.earned_rate)
        #print(single_trophy.earned_rate_percentage)
    
    for title in get_trophy_summary_for_title:
        print(title)

    #client = psnawp.me()
    for trophy_title in client.trophy_titles_for_title(title_ids=['PPSA01750_00']):
        print(trophy_title)
    print()
    #trophies = client.title_stats(limit=200)
    #for trophy in trophies:
    #    print(trophy)
    #trophies = client.e
    trophies = client.trophy_summary()
    #for trophy in trophies:
    print(trophies)
    print()
    qwe = client.trophy_groups_summary(np_communication_id=np_communication_id, platform=platform)
    
    print(qwe)
    print()


    asd = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=False)
    for trophy in asd:
        print(trophy)
        print()
    print()
    

    zxc = psnawp.game_title(title_id='PPSA01750_00', account_id='7534350400202456599', np_communication_id=np_communication_id)
    #for trophy in zxc:
    print(zxc.trophies(platform="PS5"))
    for trophy in zxc.trophies(platform="PS5"):
        print(trophy)
        print()
    print()
    #print(zxc.get_details())
    print()
    #print(zxc.trophy_groups_summary(platform="PS5"))
    print()
    for trophy_title in client.trophy_titles(limit=1):
        print(trophy_title)
    """
    #await send_to_chat('2023-04-2814:17:57.212988.html', game_name="Sackboy", platform="PS4", user_name="gorcheque", platinum=False)
    await friends_check()
            

@dp.message_handler(commands=["erase"])
async def erase(message):
    all_stats.delete_many({})
    users_collection.delete_many({})
    await bot.send_message(message.chat.id, "DB erased")



async def background_on_start() -> None:
    """background task which is created when bot starts"""

    while True:
        try:
            await friends_check()
        except Exception as e:
            print(e)
        await asyncio.sleep(350 + random.randint(1, 10))


async def on_bot_start_up(dispatcher: Dispatcher) -> None:
    """List of actions which should be done before bot start"""
    asyncio.create_task(background_on_start())  # creates background task


executor.start_polling(dp, on_startup=on_bot_start_up)
