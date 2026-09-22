# Web3 Project Scout

Two files matter:

- `bot.py` — the whole bot
- `requirements.txt` — libraries Railway installs

## On iPhone — you only need 1 variable

In Railway → Variables:

```
TELEGRAM_BOT_TOKEN=paste_your_botfather_token
```

That is enough.

The first person who sends `/start` becomes the owner. After that the bot is private.

## Optional variables — skip these

| Variable | Do you need it? |
|---|---|
| `ALLOWED_USER_IDS` | No. Only if you want to lock the bot before first `/start`. Get the number from @userinfobot |
| `ALERT_USER_IDS` | No. Alerts go to the owner automatically |
| `DATABASE_PATH` | No. Defaults to `scout.db` so the bot does not crash if `/data` does not exist |
| `DISCOVERY_INTERVAL_SEC` | No. Default `120` |
| `REQUIRE_SOCIAL_OR_WEBSITE` | Removed. Qualified = has website or socials. Built into the code |
| `ALERTS_ENABLED` | No. Use `/alerts on` or `/alerts off` in Telegram |

## iPhone deploy

1. GitHub app or safari → new repo → upload `bot.py` and `requirements.txt` (and the other small files if you have them).
2. Railway app → New Project → GitHub repo.
3. Variables → add only `TELEGRAM_BOT_TOKEN`.
4. Settings → Custom Start Command:

   `python bot.py`

5. Deploy. Open Logs. Wait for `Logged in as @YourBot`.
6. In Telegram send `/start` then wait 2 minutes then `/newtokens 12h`.

If Railway says it is not listening on a port, ignore that. This bot uses polling. It is not a website.

Later, if you want history to survive redeploys, add a Railway Volume at `/data` and then add:

```
DATABASE_PATH=/data/scout.db
```
