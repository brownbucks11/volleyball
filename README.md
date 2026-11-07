# 13U Announcements (volleyball)

Static page (GitHub Pages serves `docs/`) that shows only the club GroupMe announcements relevant to 13U.
A GitHub Actions bot polls GroupMe about every 15 minutes with your personal token and commits the matches.

## One-time setup
1. Create a new GitHub repo from these files, push, then Settings → Pages → Branch `main`, folder `/docs`.
2. https://dev.groupme.com → **Access Token** (top right). Repo Settings → Secrets → Actions → `GROUPME_TOKEN`.
3. (Optional) Make a private 13U GroupMe group, create a bot in it at https://dev.groupme.com/bots/new, and add its Bot ID as secret `GROUPME_BOT_ID` — matches get reposted there as well.
4. Find the club group's ID: `GROUPME_TOKEN=... python fetch.py --list-groups`; put it in `config.json`.
5. Edit `keywords` / `senders` in `config.json` (your coach's exact GroupMe display name keeps everything they post).
6. Actions → "Fetch announcements and rebuild site" → Run workflow. The site builds a couple of minutes later.

## Tuning
The **Skipped** tab on the site shows the last 100 messages the filter dropped — if one should have been kept,
add a keyword or sender and commit `config.json`. Local preview: `python build_site.py` then open `docs/index.html`.
The bot owns `data/` and `docs/data.js`; don't commit edits to those.

Note: GitHub Pages is public. Anyone with the URL can read the feed, so keep the repo name unguessable
(or make the repo private and view the page via the Actions artifacts / a private host instead).
