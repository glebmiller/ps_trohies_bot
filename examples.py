

from psnawp_api import PSNAWP
from time import sleep
import json 
from enum import Enum
from datetime import datetime, timezone
from typing import List



psnawp = PSNAWP('f1KIpobY9YJOMbdRPbcjAJhCpQyMVdYFXBvzQAVB7zD1yrtm3m2Oe88saYDLig1l')

# This is you
client = psnawp.me()
#print(client.online_id)
#print(client.account_id)
#print(client.get_account_devices())
#print(client.get_profile_legacy())

#for friend in client.friends_list():
#    print(friend.online_id)

#sleep(300)

#print(client.friends_list())
##print(client.blocked_list())
#print(client.available_to_play())
#groups = client.get_groups()
#print(groups)

# Getting user from online
#example_user_1 = psnawp.user(online_id="No_OT1me")
#example_user_2 = psnawp.user(online_id="test")
#print(example_user_1.online_id)
#print(example_user_1.account_id)
#print(example_user_1.profile())
#print(example_user_1.prev_online_id)
##print(example_user_1.get_presence())
#print(example_user_1.friendship())
#print(example_user_1.is_blocked())

#print(example_user_1.trophy_summary())

print()

#class PlatformType(Enum):
#    PS_vita = 'PS Vita'
#    PS3 = 'PS3'
#    PS4 = 'PS4'
#    PS5 = 'PS5'


client = psnawp.user(online_id="gleb_miller")
#CUSA24853_00
title_id = 'NPWR00291_00'
"""
name = 'Kena: Bridge of Spirits'

find_name_and_id = psnawp.search().get_title_id(name)
print("find_name_and_id =", find_name_and_id)
print()
title_id = find_name_and_id[1]
print("title_id =", title_id)
print()
"""
import csv 
name = 'Resistance 3'
login = "gleb_miller"

def get_title_ids_by_name(name: str) -> List[str]:
    title_ids = []
    with open('/home/ubuntu/Projects/Python/psn_2.0/psnawp/PlayStation-Titles/All_Titles.tsv', newline='') as tsvfile:
        reader = csv.DictReader(tsvfile, delimiter='\t')
        for row in reader:
            if name.lower() in row['name'].lower():
                title_ids.append(row['titleId'])
    return title_ids


find_name_and_id = psnawp.search().get_title_id(name)
print("find_name_and_id =", find_name_and_id)
print()
title_id = find_name_and_id[1]
print("title_id =", title_id)
print()

trophy_groups_summary = client.trophy_groups_summary('NPWR00660_00', 'PS3', False)
print("trophy_groups_summary =", trophy_groups_summary)
print()
earned_trophies = trophy_groups_summary.earned_trophies
print(earned_trophies)


for trophy_title_info in client.trophy_titles_for_title(title_ids=[title_id]):
    print(1)
    print("trophy_title_info =", trophy_title_info)
    print()
    last_updated_date_time=trophy_title_info.last_updated_date_time
    print("last_updated_date_time =", last_updated_date_time)
    print()
    now = datetime.now(timezone.utc)
    print("now =", now)
    print()
    print("now - last_updated_date_time =", now - last_updated_date_time)
    print()


#all trophies
for trophy_title in client.trophy_titles(limit=None):
    if trophy_title.title_name == 'SOMA':
        print("trophy_title =", trophy_title)
        print()
        game_name = trophy_title.title_name
        np_communication_id = trophy_title.np_communication_id
        platform = next(iter(trophy_title.title_platform)).value
        print("game_name =", game_name)
        print()
        print("np_communication_id =", np_communication_id)
        print()
        print("platform =", platform)
        print()


"""
    #sleep(300)
    print(platform) 


    print("game_name =", game_name)
    print()
    print("np_communication_id =", np_communication_id)
    print()
    print("platform =", platform)
    print()


    find_name_and_id = psnawp.search().get_title_id(game_name)
    print("find_name_and_id =", find_name_and_id)
    print()
    title_id = find_name_and_id[1]
    print("title_id =", title_id)
    print()

    for trophy_title_info in client.trophy_titles_for_title(title_ids=[title_id]):
        print("trophy_title_info =", trophy_title_info)
        print()
        last_updated_date_time=trophy_title_info.last_updated_date_time
        print("last_updated_date_time =", last_updated_date_time)
        print()
        now = datetime.now(timezone.utc)
        print("now =", now)
        print()
        print("now - last_updated_date_time =", now - last_updated_date_time)
        print()


    game_trophies = client.trophies(np_communication_id=np_communication_id, platform=platform, include_metadata=True)
    #print(trophs)
    for single_trophy in game_trophies:
        print("single_trophy =", single_trophy)
        print()

    trophy_groups_summary = client.trophy_groups_summary(np_communication_id=np_communication_id, platform=platform, include_metadata=True)
    print("trophy_groups_summary =", trophy_groups_summary)
    print()

    title_stats = client.title_stats(limit=1)
    #print("title_stats =", title_stats)
    #print()
    for title in title_stats:
        print("title =", title)
        print()

"""

  
#trophies(np_communication_id: str, platform: Literal['PS Vita', 'PS3', 'PS4', 'PS5'], trophy_group_id: str = 'default', limit: Optional[int] = None, include_metadata: bool = False) → Iterator[Trophy][source]¶


#all_user_trophies = example_user_1.trophy_titles()


#game_trophy = example_user_1.trophy_titles_for_title('NPWR22392_00')
#print(list(game_trophy))


#for trophy_title in example_user_1.trophy_titles_for_title('NPWR22392_00'):
#    print(trophy_title)


#for trophy in all_user_trophies:
###    print(trophy)
#    sleep(300)

# Getting user from Account ID
#user_account_id = psnawp.user(account_id='9122947611907501295')
#print(user_account_id.online_id)

# Sending Message
#group = psnawp.group(group_id='38335156987791a6750a33ae452ec8666177b65e-103')
##print(group.get_group_information())
#print(group.get_conversation(10))
#print(group.send_message("Hello World"))
#print(group.change_name("API Testing 3"))
#print(group.leave_group())

# Creating new group
#new_group = psnawp.group(users_list=[example_user_1, example_user_2])

#search = psnawp.search()
#print(search.get_title_details(title_id="PPSA03420_00"))
#print(search.universal_search("GTA 5"))

# Get Play Times (PS4, PS5 above only)
example_user_1 = client.title_stats()
