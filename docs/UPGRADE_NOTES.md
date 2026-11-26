# NOVA & ATLAS — Delivery report

Status words used below:
- **Tested (offline)**: covered by the automated tests in `tests/` (31 tests, all passing) using fake Discord objects and real databases. No test connected to Discord.
- **Implemented, not tested live**: code is written, loads without errors, and its commands/slash payloads validate, but it has not been exercised against a real Discord server.
- **Not implemented** / **Blocked**: as stated.

**Nothing in this report has been tested against live Discord.** I had no way to log the bots in from the build environment, so voice joining and TTS playback in a real channel still need the checklist in section 8 on your PC.

## 1. Files inspected
Every file in the upload: `bot.py`, `nova.py`, `atlas_stats.py`, `atlas_stats_core.py`, `config.py`, `ai_approval.py`, `hinglish.py`, `vibes.py`, `priority.py`, `install_assets.py`,
`setup.bat`, `start_atlas.bat`, `start_nova.bat`, `START_VOID.bat`, `requirements.txt`, `README.md`, `.gitignore`, `config.json`, `void_data.json`, `atlas_stats.json` (empty),
`nova_state.json`, `nova_tts.json`, `nova_colours.json`, `.env` (key names only, never the value), `token.txt`/`token2.txt` (both empty).
I also read discord.py 2.7.1's voice reconnect source to find the voice bug.

## 2. Files created
- `common/` — `db.py` (SQLite, migrations, validated backups, per-server settings), `config.py` (Discord settings panel), `checks.py` (central permission and hierarchy checks),
  `ui.py` (embeds, confirmations, pagination), `errors.py` (logging with secret redaction, error IDs), `voice.py` (disconnect classification, backoff, connect helper),
  `timeparse.py`, `diag.py` (diagnostics, preferences, config export/import).
- `atlas_ext/` — `core, voice, statsx, logs, moderation, automod, verification, onboarding, tickets, feedback, polls, events, reminders, engagement, starboard,
  games, game_data, utility, announce, rules, embeds`.
- `nova_ext/` — `core, voice, roles, tempvoice, channels`.
- `tests/` (4 files), `DELIVERY_REPORT.md`.
- About 9,600 lines of new code in total.

## 3. Files modified
- `bot.py` (+251/−314 lines): economy helpers removed; voice watchdog, disconnect handling, `!join` and the TTS worker made reconnect-tolerant; extension loader;
  logging; error IDs; moderation case hooks (one `dispatch` line per existing mod command); help rewritten as pages. `!trivia`, `!remind`, `!serverinfo`, `!userinfo` and `!avatar`
  moved into extensions with the **same names**. The old `!remind` sent every reminder twice and forgot them on restart. **The AI code is byte-identical** (an automated test compares it).
- `nova.py` (+136/−20): now a `commands.Bot`, which is a `discord.Client`, so every existing handler works unchanged. Same voice fixes; volume; extension loader;
  Members-intent fallback; `!mclearrolecolour` also clears gradient colours (still colour-only).
- `config.py`: 18 economy constants removed. AI settings unchanged.
- `requirements.txt` (+`tzdata`), `README.md` (rewritten, AI/TTS/visibility sections kept word for word), `.gitignore`.

## 4. Files deleted
None. `.env` is left out of the zip because it holds ATLAS's token. Keep your existing copy. **Because the token was inside the zip you uploaded, consider resetting it in the Developer Portal.**

## 5. Features implemented
| Area | Status |
|---|---|
| Voice reliability fix (both bots), voice status/diagnostics, queue, skip/stop/pause/resume, volume | Disconnect logic: **tested (offline)**. Playback controls: implemented, not tested live |
| NOVA owner-only `!mclearrolecolour` (the configured `MODERATION_OWNER_ID`) | **Tested (offline)**: 3 other IDs rejected, missing Manage Roles rejected, hierarchy/managed/default skipped, colour-only edit, cancel = no change, not a slash command |
| ATLAS economy & gambling removal | **Tested (offline)**: data archived and verified, economy commands absent, all 64 original commands present |
| ATLAS statistics extension (`/stats` daily/weekly/monthly/alltime/member/channel/voice/joins/report, trends, charts, join/leave tracking, scheduled reports) | **Tested (offline)** |
| NOVA role menus (buttons/dropdown, toggle/unique/add-only, requirements, validation), reaction roles, temporary roles, templates, bulk add/remove, role info/hierarchy/permission/eligibility diagnostics, role logs & history, colour-role cleanup | Menus: **tested (offline)** (unique swap, staff-permission role refused, history). Rest implemented, not tested live |
| Temporary voice channels (join-to-create, rename, limit, lock, hide, transfer, claim, restart sweep; only NOVA-created rooms are ever deleted) | Implemented, not tested live |
| Onboarding extras (custom text, welcome card, account age, guide DM, goodbye, newcomer nudge) | Card & templates **tested (offline)**; rest not tested live |
| Verification (button, rules acceptance, waiting period, account-age manual review, grant-all lock-out protection, join-spike alerts, stats) | Implemented, not tested live |
| Moderation (case numbers, mod log, notes, softban, modhistory, review queue with buttons, DM with appeal info) | Implemented, not tested live |
| Automod (words, spam, mentions, duplicates, links, invites, exemptions; delete + notify + review, never auto-punish by default) | Rules **tested (offline)** |
| Logging (members, roles, nicknames, messages without content by default, channels, roles/permissions, voice, config changes) | Implemented, not tested live |
| Tickets (panel, categories, private channels, claim/assign/priority/close/reopen, transcripts, inactivity reminders, feedback, stats, archive) | Implemented, not tested live |
| Suggestions (named/anonymous, voting, statuses, duplicate detection, threads, stats) & private reports (`/modreport`, "Report message" menu) | Similarity **tested (offline)**; rest not tested live |
| Button polls (multi/yes-no, anonymous, multiple choice, expiry, vote change, results, history) | **Tested (offline)** |
| Events (RSVP, timezone-aware reminders, edit, cancel, history, stats) | Implemented, not tested live |
| Persistent reminders (personal, server, recurring, edit/move/cancel) | Time parsing **tested (offline)** |
| Engagement (daily question, starters, milestones, join anniversaries, recognition, featured messages, weekly highlights, challenges) | Implemented, not tested live |
| Starboard, message of the day, bookmarks (context menu + `!save`) | Implemented, not tested live |
| Games: tic-tac-toe, Connect Four, RPS duel, trivia (43 built-in + custom, categories, difficulty, explanations), team trivia, daily quiz, number guess, hangman, emoji guess, riddles, memory, reaction test, word chain, leaderboards, match history | Win logic **tested (offline)** |
| Utilities (rich serverinfo/userinfo/avatar, banner, profile, roles, emoji tools, quote, fun facts, command search, feature request, bug report) | Implemented, not tested live |
| Announcements (daily/weekly/monthly/interval/once, preview, formatted post) & feeds (YouTube, RSS, Twitch) | Scheduling & feed parsing **tested (offline)** |
| Rules (versioned, accept button, lookup, editor, publish, acceptance stats) | Implemented, not tested live |
| Embed builder (modals, preview, limits, publish confirmation, edit existing) | Implemented, not tested live |
| Settings panel (`!config`, `!mconfig`, `/config`), diagnostics (`!diag`, `/diag`), preferences, config export/import/backup history | Module isolation **tested (offline)** |
| Channel/message tools (info, topic, slowmode, timed lock, cleanup preview, threads, auto-threads, forum posts, pins, search, jump, sticky) | Implemented, not tested live |

## 6. Features restored
- 24/7 voice auto-rejoin for both bots. The stored sessions were all stuck on `paused` by the bug and are resumed once on first start; anything you paused with `!leave`/`!mleave` stays paused.
- Discord library logs are now written to `logs/` (they were switched off before, which hid voice errors).
- `!coinflip`: the old economy cleanup had removed this wager-free command too, so it is available again.

## 7. Features not implemented / partial
- **Mini tournaments**: not implemented.
- **Per-role feature restrictions**: partial. There are allowed game channels, automod exempt roles/channels, role-menu requirements and staff-only creation options, but no general per-role or per-channel switch for every command.
- **Bot errors and voice failures in a Discord log channel**: partial. They go to `logs/*.log` and `!diag errors`, not to a Discord channel.
- **Slowmode reminders**: partial. A notice is posted when slowmode changes; there is no repeating reminder.
- **Restricted channels for unverified users**: by design, the bot never edits channel permissions. You restrict channels to the verified role in Discord; run `!verify grantall` first.
- **Twitch alerts**: blocked until you set the `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` environment variables (free Twitch developer app). YouTube and RSS need nothing.

## 8. Voice testing — NOVA
Not tested live. Offline: a reconnect is not treated as a removal; a moderator disconnect is; the 4 classification paths and the backoff behave correctly.
**Please run on your PC:** start both bots → `!mjoin` in a voice channel → `!mvstatus` → type in #nova-tts → `!mvdiag`.

## 9. Voice testing — ATLAS
Not tested live. Offline: a reconnect doesn't pause her; a moderator disconnect does (and posts the message); NOVA leaving voice doesn't affect ATLAS.
**Please run:** `!join` → `!vstatus` → speak in the TTS channel → `!leave` (NOVA must stay connected) → restart both → both rejoin within about a minute.

## 10. TTS testing
Not tested live (no Piper model or Discord voice in the build environment). The Piper/FFmpeg pipeline is unchanged; only reconnect waiting, retry, volume (`-filter:a volume`) and queue status were added.

## 11. Statistics migration
No migration. `atlas_stats.json` is read in place and never rewritten by the new code. New join/leave data lives in `atlas.db`. Reports say what was tracked, what was recovered (messages via `!kavstatbackfill`) and what can't be recovered.
Note: the uploaded `atlas_stats.json` is empty (0 bytes), so there was no history to preserve in this copy.

## 12. Role-colour command authorization results
Tested (offline), see section 5. Authorization is the exact configured `MODERATION_OWNER_ID` only (not username or admin rights), it is checked again on the confirm button, and it is not registered as a slash command.

## 13. Economy removal results
On first start ATLAS backs up `void_data.json` and writes `backups/economy_archive_<time>.json` with the removed data (balances, daily, work, transactions, games, economy totals: 1 user, 55,000 KO).
She checks the archive reads back identically, then removes those keys. All other data stays byte-identical (verified: 101 welcomed members, 6 cases, 2 warnings, 2 birthdays). Removed: 13 helper functions, 18 constants, the blackjack error branch.

## 14. Database & backups
New `atlas.db` and `nova.db` (SQLite, WAL, indexed, every row keyed by server ID). Every pending migration first takes an online backup, which is integrity-checked; the last 20 are kept in `backups/`.
JSON files touched on first start (each backed up first): `void_data.json` (economy keys), `config.json` and `nova_state.json` (voice session reset + a `_voice_fix_version` marker).
`!configexport` / `!configimport` / `!configbackups` for settings (preview, confirmation, automatic pre-import backup). Tokens are never exported.

## 15. Dependencies changed
Added `tzdata` (timezone data; Windows has none built in). Everything else was already present: aiohttp comes with discord.py; matplotlib/Pillow are installed by the existing stats code.

## 16. Discord Developer Portal & permissions
- ATLAS: Server Members + Message Content intents (required); Presence optional.
- NOVA: Message Content (required); **Server Members recommended** (if it's off, NOVA writes `.nova_no_members` and restarts in limited mode).
- Both: re-invite with `applications.commands` if slash commands don't appear. Give **View Audit Log** so moderator disconnects can be confirmed.
- Role menus, verification, temporary roles and automod timeouts need each bot's highest role **above** the roles it manages.

## 17. Remaining issues (please review)
- **Policy conflict, kept as is:** ATLAS's existing `!report` puts a 10-minute timeout on someone based on one member's report. The spec says reports shouldn't punish automatically, but it also says to keep existing features. I left `!report` untouched; the new `/modreport` never punishes. Your call whether to change `!report`.
- **AI (reported only, not changed):** `!aidm` and `!aisay` have no owner/admin check (any member can trigger the approval window on your PC). The approval window is Windows-only.
- **Existing privacy features, unchanged:** DM forwarding sends DM text to a staff channel; `!visibility` DMs recent message text to admins.
- ATLAS's `!kick`/`!ban` (and the new `!softban`) stay limited to `MODERATION_OWNER_ID`, as before.
- Duplicate `on_guild_join` handler and a few unused imports were already in the original and were left alone.

## 18. Exact commands

### ATLAS
**Announcements**
- `!announce` (aliases: announcement)
- `!announce add <channel> <spec>`
- `!announce list`
- `!announce post <channel> <text>`
- `!announce remove <aid>` (aliases: delete)
- `!announce test <aid>`
- `!announce toggle <aid>`
- `!feeds` (aliases: feed)
- `!feeds add <kind> <channel> <source> [label]`
- `!feeds health`
- `!feeds ping <fid> [role]`
- `!feeds remove <fid>`
- `!feeds template <fid> [template]`

**AutoMod**
- `!automod`
- `!automod test <text>`
- `!automod words`
- `!automod words add <words>`
- `!automod words remove <words>`

**Core**
- `!config [module]` (aliases: settings)
- `!configbackups [restore_id]`
- `!configexport`
- `!configimport`
- `!diag [section]` (aliases: diagnostics, botinfo)
- `!prefs` (aliases: preferences)
- `!timezone [zone]` (aliases: tz)

**Core (bot.py)**
- `!8ball <question>` (aliases: ask)
- `!afk [reason=AFK]`
- `!ai <prompt>`
- `!aidm <member> <text>`
- `!aioff`
- `!aion`
- `!aisay <text>`
- `!ban <member> [reason=No reason provided]`
- `!birthday <date_text>`
- `!birthdaychannel <channel>`
- `!choose <options>`
- `!clearchannel <channel>` (aliases: clearhistory)
- `!clearwarnings <member>`
- `!coinflip [side=heads]` (aliases: cf)
- `!compliment [member]`
- `!dmforwardtest`
- `!forgive <member> [reason=Apology accepted / staff override]`
- `!help [category]` (aliases: commands, h)
- `!highfive <member>`
- `!hug <member>`
- `!join`
- `!joke`
- `!kick <member> [reason=No reason provided]`
- `!leave` (aliases: disconnect)
- `!love [member]` (aliases: crush, compatibility)
- `!okay [phrase]`
- `!ping`
- `!poll <question>`
- `!postrules`
- `!prefix <new_prefix>`
- `!purge <count>`
- `!rebuild`
- `!report <member> [details=No details supplied]` (aliases: bullyreport)
- `!resetprefix`
- `!rewelcome`
- `!roast [member]` (aliases: roastme)
- `!roles [action=list] [role_text]` (aliases: selfroles)
- `!roll [sides=6]`
- `!rps <choice>`
- `!selfrole [action=list] [role_text]` (aliases: selfroleset)
- `!shoutout <member> [reason=for being part of the community]`
- `!slowmode <seconds>`
- `!timeout <member> <duration> [reason=No reason provided]`
- `!ttschannel [channel]`
- `!ttsdisable`
- `!unban <user_id>`
- `!untimeout <member> [reason=Timeout lifted by staff]`
- `!vibe`
- `!vibechannel <channel>`
- `!vibenow`
- `!visibility`
- `!voicemute <member>` (aliases: vmute, servermute)
- `!voiceunmute <member>` (aliases: vunmute, serverunmute)
- `!warn <member> [reason=No reason provided]`
- `!warnings <member>`
- `!welcome_missing`
- `!welcomechannel [channel]`
- `!welcometest`
- `!wyr` (aliases: wouldyourather)

**Embeds**
- `!embed [channel]` (aliases: embedbuilder)
- `!embededit <link>`

**Engagement**
- `!challenge`
- `!challenge end`
- `!challenge join [note]` (aliases: done, submit)
- `!challenge set <spec>`
- `!feature <link>`
- `!highlights`
- `!question` (aliases: qotd, randomquestion)
- `!questionadd <text>`
- `!recognize <member> <reason>` (aliases: recognise, kudos)
- `!topic` (aliases: starter)

**Events**
- `!event` (aliases: events)
- `!event cancel <event_id> [reason]`
- `!event create <spec>` (aliases: new)
- `!event edit <event_id> <field> <value>`
- `!event info <event_id>`
- `!event list` (aliases: upcoming)
- `!event past` (aliases: history)
- `!event stats`

**Feedback**
- `!bugreport <problem>` (aliases: reportproblem)
- `!featurerequest <idea>` (aliases: fr)
- `!myreports`
- `!suggest <text>`
- `!suggestion [number]`
- `!suggestion approve <number> [response]`
- `!suggestion consider <number> [response]`
- `!suggestion duplicate <number> [response]`
- `!suggestion implement <number> [response]`
- `!suggestion reject <number> [response]`
- `!suggestions [status=open]`
- `!suggestionstats`

**Games**
- `!c4 <opponent>` (aliases: connect4, connectfour)
- `!dailyquiz`
- `!emojiguess` (aliases: emojiquiz)
- `!gamehistory [member]` (aliases: matches)
- `!gamesboard [game]` (aliases: gameslb, gameleaderboard)
- `!guessnumber [maximum=100]` (aliases: numberguess, gn)
- `!hangman` (aliases: wordguess)
- `!memory [length=5]`
- `!reaction` (aliases: reactiontest, fastest)
- `!riddle`
- `!rpsduel <opponent>` (aliases: rpsvs)
- `!trivia [category] [difficulty]`
- `!triviaadd <spec>`
- `!triviaremove [question_id]`
- `!triviateam [rounds=5] [teams]` (aliases: triviaparty, quiz)
- `!ttt <opponent>` (aliases: tictactoe)
- `!wordchain`
- `!wordchain start`
- `!wordchain stop`

**Moderation**
- `!case <number>`
- `!cases [member]`
- `!delnote <note_id>`
- `!modhistory <member>` (aliases: history, offender)
- `!note <member> <text>`
- `!notes <member>`
- `!reason <number> <text>`
- `!reviewqueue [status=open]` (aliases: rq)
- `!rqclose <item_id> [status=resolved]`
- `!softban <member> [reason=No reason provided]`

**Polls**
- `!polls`
- `!polls end <poll_id>` (aliases: close)
- `!polls list [status=open]` (aliases: history)
- `!polls new <spec>` (aliases: create)
- `!polls results <poll_id>`
- `!polls yesno <spec>`

**Reminders**
- `!remind <when> <text>`
- `!reminder`
- `!reminder cancel <rid>` (aliases: delete, remove)
- `!reminder edit <rid> <text>`
- `!reminder move <rid> <when>` (aliases: reschedule)
- `!reminders` (aliases: myreminders)
- `!remindevery <interval> <text>` (aliases: recurring)
- `!serverremind <channel> <spec>` (aliases: sremind)

**Rules**
- `!rule <number>`
- `!rules`
- `!rules draft`
- `!rules history`
- `!rulesedit [text]`
- `!rulespublish`
- `!rulesstatus`

**Starboard**
- `!bookmarks`
- `!save [link] [note]` (aliases: bookmark)
- `!starboardtop` (aliases: startop)
- `!unbookmark <bookmark_id>`

**StatsExtension**
- `!stats [period=7d]`
- `!stats alltime`
- `!stats channel [channel] [period=30d]`
- `!stats daily`
- `!stats joins [days=30]`
- `!stats member [member] [period=30d]`
- `!stats monthly`
- `!stats report [period=7d]`
- `!stats voice [period=30d]`
- `!stats weekly`

**Tickets**
- `!ticket`
- `!ticket add <member>`
- `!ticket archive`
- `!ticket category`
- `!ticket category add <emoji> <rest>`
- `!ticket category remove <name>`
- `!ticket claim`
- `!ticket close`
- `!ticket priority <level>`
- `!ticket remove <member>`
- `!ticket reopen`
- `!ticket stats [days=30]`
- `!ticket transcript`
- `!ticket transfer <staff_member>` (aliases: assign)
- `!ticketpanel [channel]`

**Utility**
- `!avatar [member]`
- `!banner [member]`
- `!commandsearch <word>` (aliases: findcommand, searchcmd)
- `!emoji <emoji>` (aliases: enlarge, bigemoji)
- `!emojis` (aliases: emojilist)
- `!funfact` (aliases: fact)
- `!profile [member]`
- `!quote <link>`
- `!roleslist [member]` (aliases: myroles)
- `!serverinfo`
- `!userinfo [member]`

**Verification**
- `!verify`
- `!verify approve <member>`
- `!verify deny <member> [reason=Not approved]`
- `!verify grantall`
- `!verify panel [channel]`
- `!verify stats [days=30]`

**Voice**
- `!pause`
- `!queue` (aliases: ttsqueue)
- `!resume`
- `!skip`
- `!stop` (aliases: ttsstop)
- `!vdiag` (aliases: voicediag)
- `!volume [percent]` (aliases: vol)
- `!vstatus` (aliases: voicestatus)

**Slash / context menu:** `/8ball`, `/afk`, `/avatar`, `/banner`, `/choose`, `/coinflip`, `/compliment`, `/config`, `/diag`, `/eventcreate`, `/help`, `/highfive`, `/hug`, `/joke`, `/love`, `/modreport`, `/ping`, `/poll`, `/pollnew`, `/prefs`, `/profile`, `/remind`, `/roast`, `/roll`, `/rps`, `/serverinfo`, `/shoutout`, `/stats alltime`, `/stats channel`, `/stats daily`, `/stats joins`, `/stats member`, `/stats monthly`, `/stats overview`, `/stats report`, `/stats voice`, `/stats weekly`, `/stats`, `/suggest`, `/timezone`, `/userinfo`, `/vibe`, `/wyr`, context menu “Bookmark message”, context menu “Report message”

### NOVA (original commands, unchanged)
`!pfp [@member|ID]`, `!mjoin`, `!mleave`, `!mtts`, `!mttsdisable`, `!colours` / `!colors`, `!mclearrolecolour` / `!mclearrolecolor` (owner ID only), `/clear`

### NOVA (new)
**Channels**
- `!marchive [channel] [inactive]`
- `!mautothread [channel] [state=on]`
- `!mchannelinfo [channel]` (aliases: mchinfo)
- `!mcleanup [amount=50] [filters]` (aliases: mpurge)
- `!mforumpost <forum> <text>`
- `!mjump [channel] [message_id]`
- `!mlock [channel] [duration] [reason]`
- `!mpins [channel]`
- `!msearch [channel] <query>`
- `!mslowmode [duration=0] [channel]`
- `!msticky [text]`
- `!mthread [name]`
- `!mtopic [channel] [text]` (aliases: mdescription)
- `!munlock [channel]`

**Core**
- `!mconfig [module]` (aliases: msettings)
- `!mconfigbackups [restore_id]`
- `!mconfigexport`
- `!mconfigimport`
- `!mdiag [section]` (aliases: mdiagnostics, mbotinfo)
- `!mprefs` (aliases: mpreferences)
- `!mtimezone [zone]` (aliases: mtz)

**Roles**
- `!mbotroles [channel]` (aliases: mpermcheck)
- `!mbulkrole <action> <role> [target=humans]`
- `!mchannelperms <channel> [target]`
- `!mpermcompare <a> <b>`
- `!mreactionrole` (aliases: mrr)
- `!mreactionrole add <link> <emoji> <role>`
- `!mreactionrole remove <link> <emoji>`
- `!mrolecleanup`
- `!mroleeligible <member> <role>` (aliases: mcanget)
- `!mrolehierarchy` (aliases: mhierarchy)
- `!mrolehistory <target>`
- `!mroleinfo <role>`
- `!mrolemenu` (aliases: mrm)
- `!mrolemenu add <menu_id> <role> [rest]`
- `!mrolemenu create <style> <text>`
- `!mrolemenu delete <menu_id>`
- `!mrolemenu list`
- `!mrolemenu mode <menu_id> <mode>`
- `!mrolemenu post <menu_id> [channel]`
- `!mrolemenu preview <menu_id>`
- `!mrolemenu refresh <menu_id>`
- `!mrolemenu remove <menu_id> <role>`
- `!mrolemenu require <menu_id> <role>`
- `!mrolemenu validate <menu_id>`
- `!mroleperms <role>`
- `!mroletemplate` (aliases: mrt)
- `!mroletemplate apply <name>`
- `!mroletemplate delete <name>`
- `!mroletemplate save <name> [roles]...`
- `!mroletemplate show <name>`
- `!mtemprole <member> <role> <duration> [reason]`
- `!mtemprolecancel <temp_id> [keep=remove]`
- `!mtemproles`

**TempVoice**
- `!mvc` (aliases: mtempvc)
- `!mvc claim`
- `!mvc hide`
- `!mvc hubs`
- `!mvc limit <limit>`
- `!mvc lock`
- `!mvc name <name>` (aliases: rename)
- `!mvc panel`
- `!mvc removehub <hub>`
- `!mvc setup <hub> [category] [template]`
- `!mvc show` (aliases: unhide)
- `!mvc transfer <member>`
- `!mvc unlock`

**Voice**
- `!mafk [channel] [minutes=5]`
- `!mpause`
- `!mqueue`
- `!mresume`
- `!mskip`
- `!mstop`
- `!mvdiag` (aliases: mvoicediag)
- `!mvoiceinfo [channel]` (aliases: mvcinfo, mwhoisin)
- `!mvolume [percent]` (aliases: mvol)
- `!mvstatus` (aliases: mvoicestatus)

**Slash / context menu:** `/clear`, `/config`, `/diag`, `/prefs`, `/timezone`
