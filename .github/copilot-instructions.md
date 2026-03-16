# PSN Trophies Telegram Bot

## Project Overview
Telegram bot that monitors PlayStation Network friends' trophy activity and sends notifications to a Telegram chat. Uses MongoDB for state tracking and a local fork of [psnawp](https://github.com/isFakeAccount/psnawp) v3.0.1 for PSN API access.

## Architecture
- **psn_bot.py** — Main bot file (aiogram v2, single-file bot)
- **database.py** — Config module reading env vars (NPSSO_CODE, BOT_TOKEN, CHAT_ID, AMAZON_URL)
- **src/psnawp_api/** — Local fork of PSNAWP library (v3.0.1). PSN API wrapper with OAuth2 auth, trophy/user/search models, built-in rate limiting via pyrate_limiter
- **PlayStation-Titles/** — TSV lookup table for title IDs (fuzzy matched via Levenshtein distance)
- **blank_page.html** — HTML template for trophy notification pages

## PSNAWP v3 Key Types
- `PlatformType` enum — PS3, PS4, PS5, PS_VITA, PSPC, UNKNOWN (values: "PS3", "PS4", "PS5", "PSVITA", "PSPC", "UNKNOWN")
- `TrophySet` dataclass — `bronze`, `silver`, `gold`, `platinum` (int fields)
- `SearchDomain` enum — FULL_GAMES=0, ADD_ONS=1, USERS=2
- `Trophy` vs `TrophyWithProgress` — progress fields (earned_date_time, trophy_earn_rate) only on TrophyWithProgress; use `include_progress=True`
- Rate limiting: built-in 1 req/3 seconds via SQLite-backed pyrate_limiter bucket

## Data Flow
1. Background loop (`friends_check()`) polls PSN friends every ~350s
2. Compares `last_updated_datetime` from PSN API against `date_added` in MongoDB
3. New trophies → generates HTML page → copies to web server → sends Telegram message with link
4. Platinum trophies get a special sticker notification

## MongoDB Collections (db: PSNTrophies_new)
- `games` — keyed by `np_communication_id`, stores title/platform/image/user progress
- `users` — keyed by `online_id`, stores `date_added` timestamp

## Key Conventions
- PSN user objects: use `psn_user` variable name (not `client`) to avoid shadowing `pymongo.MongoClient`
- Use `logging` module instead of `print()` for all output
- Use `await asyncio.sleep()` in async functions, never blocking `time.sleep()`
- Always use `re.escape()` when building regex from user input
- Bare `except:` clauses are forbidden — always catch specific exceptions
- File paths must be relative to project root, never hardcoded absolute paths
- `platform` param requires `PlatformType(platform)` wrapper when passed to v3 API calls
- Use `include_progress=True` on `trophies()` calls to get earned dates and earn rates

## Commands
- `/add <username>` — Add PSN friend to tracking DB (runs async, won't block bot)
- `/find <game>` — Search DB for game, show progress with platinum indicators
- `/del <username>` — Remove user (admin only)
- `/refresh` — Re-fetch all users' progress from PSN, catches DLC percentage changes (admin only)
- `/psn_search <name>` — Search PSN for users by partial name (uses GraphQL search API)
- `/status` — Show online/offline status and current game for all tracked users
- `/erase` — Wipe DB (admin only)

## Environment Variables
```
NPSSO_CODE=<64 char PSN npsso token>
BOT_TOKEN=<Telegram bot token>
CHAT_ID=<Telegram chat ID for notifications>
AMAZON_URL=<URL prefix for trophy page links>
WEB_ROOT=<path to web server root, default /var/www/millertech>
```

## Running
```bash
# Set env vars (or use .env file)
export NPSSO_CODE=...
export BOT_TOKEN=...
export CHAT_ID=...
python psn_bot.py
```

## Known Limitations
- NPSSO token expires every ~2 months (refresh token). Bot logs warning when <3 days remain
- TSV title lookup requires `PlayStation-Titles/All_Titles.tsv` to be populated
- aiogram v2 is legacy; migration to v3 is a future consideration
