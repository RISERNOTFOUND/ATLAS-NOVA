# ATLAS & NOVA — Feature Reference

> **Created by RISERNOTFOUND**

This document lists every feature found in the source code, with the exact command names, aliases and syntax extracted
from the code itself (`scripts/command_inventory.py` regenerates the raw list in `docs/command_inventory.json`).
**320 entries** are documented below: 317 declarations found in the source (verified by `scripts/verify_inventory.py`), where the 7 shared settings/diagnostics commands are listed once per bot (7+7 instead of 7, slash forms merged).

## How to read this document

- **Prefix:** ATLAS accepts `!`, `k`, `K`, `?`, `.` or an @mention by default (changeable per server with `!prefix`; a custom prefix replaces the defaults). NOVA accepts `!` plus the server’s custom prefix. Examples below use `!`. NOVA’s newer commands start with **m** (`!mrole…`, `!mvc…`) so they never clash with ATLAS.
- **Access:** what the code checks. “Everyone” means no extra permission check in the command itself (Discord channel permissions still apply).
- **Syntax:** `<required>` `[optional]`, taken from the function signature; use `!help` / `!commandsearch` in Discord for friendly examples.
- **Status labels:** *Implemented* (present in source) · *Optional integration* (needs an outside service you provide) · *Known issue* (reported problem; see docs/TROUBLESHOOTING.md) · *Partially implemented* (a documented limit applies) · *Planned / not implemented* (nothing is promised — none of the features below is merely planned).
- **Verification note:** commands were extracted from the source automatically. The maintainer has tested the bots in a live server; the release pipeline could run only part of the automated tests (see docs/audits/TEST_REPORT.md). Nothing here claims more than the code shows.

## Not implemented (by design or limit)

- No economy, currency, betting or gambling (removed from ATLAS earlier; archived data is never published).
- No web dashboard — everything is configured inside Discord.
- Verification never edits channel permissions or removes roles (you restrict channels yourself).
- ATLAS cannot read DMs that were sent while it was offline; Discord does not provide them.
- Statistics cannot be reconstructed for periods before tracking started, except messages via `!kavstatbackfill` (last 30 days).

# ATLAS

ATLAS is the community-hub bot (`bot.py`, `atlas_ext/`, `atlas_stats*.py`). Internal module names keep their historical `atlas_` spelling so existing installs and imports keep working.

### Welcome & onboarding

- **Status:** Implemented
- **Provided by:** `bot.py`, `atlas_ext/onboarding.py`
- **What it does:** Posts one welcome for each new member and one onboarding note for existing members (duplicate-protected). `!welcometest` previews it; `!rewelcome` replaces earlier welcome posts and welcomes every current account (admin). Optional extras you can switch on in `!config onboarding`: custom welcome text, generated welcome-card image, account-age info, guide DM, goodbye message, newcomer nudge.
- **Required Discord permissions:** Server Members Intent. Bot needs View Channel, Send Messages, Embed Links (and Attach Files for the card) in the welcome channel.
- **Configuration:** `!welcomechannel #channel`; extras via `!config onboarding` (`welcome_message`, `welcome_card`, `show_account_age`, `guide_dm`, `goodbye_channel`, `goodbye_message`, `nudge_hours`, `nudge_text`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!welcomechannel` | — | `[channel]` | Manage Guild | Set the channel for welcome messages. |
| `!welcometest` | — | — | Manage Guild | Post a preview welcome (not counted as a real welcome). |
| `!welcome_missing` | — | — | Administrator | Scan the server and welcome members who never received a welcome (admin). |
| `!rewelcome` | — | — | Administrator | Replace ATLAS's prior welcome posts and welcome every current account again. |

### Vibes, birthdays & social

- **Status:** Implemented
- **Provided by:** `bot.py`
- **What it does:** Hourly “vibe” prompts in a configured channel (NOVA replies to them if it is in the server), birthday announcements, and light social/party commands (random compliments, shoutouts, roasts, compatibility game, hugs, coin/dice/8-ball/rock-paper-scissors).
- **Required Discord permissions:** Send Messages, Embed Links, Add Reactions in the target channels.
- **Configuration:** `!vibechannel #channel`, `!birthdaychannel #channel`. NOVA’s vibe replies need NOVA in the same server and the same vibe channel.
- **Works independently?** Independent (NOVA reply is optional).

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!vibe` · `/vibe` | — | — | Everyone | Get a vibe prompt |
| `!wyr` · `/wyr` | `!wouldyourather` | — | Everyone | Play a round of would-you-rather |
| `!compliment` · `/compliment` | — | `[member]` | Everyone | Send a compliment to a member or yourself |
| `!shoutout` · `/shoutout` | — | `<member> [reason]` | Everyone | Give a community shoutout to a member |
| `!birthday` | — | `<date_text>` | Everyone | Save your birthday as MM-DD, or `birthday remove`. |
| `!vibechannel` | — | `<channel>` | Manage Guild | Set the channel for the hourly vibe posts. |
| `!birthdaychannel` | — | `<channel>` | Manage Guild | Set the channel for birthday announcements. |
| `!joke` · `/joke` | — | — | Everyone | Hear a joke from ATLAS |
| `!roast` · `/roast` | `!roastme` | `[member]` | Everyone | Playfully roast a member or yourself |
| `!love` · `/love` | `!crush`, `!compatibility` | `[member]` | Everyone | Check compatibility with another member |
| `!hug` · `/hug` | — | `<member>` | Everyone | Send a friendly hug to someone |
| `!highfive` · `/highfive` | — | `<member>` | Everyone | Give a high five to someone |
| `!coinflip` · `/coinflip` | `!cf` | `[side]` | Everyone | Flip a coin |
| `!8ball` · `/8ball` | `!ask` | `<question>` | Everyone | Ask the magic 8-ball a question |
| `!roll` · `/roll` | — | `[sides]` | Everyone | Roll a die |
| `!rps` · `/rps` | — | `<choice>` | Everyone | Play rock, paper, scissors |
| `!vibenow` | — | — | Everyone | Post a vibe right now (handy for testing NOVA's reply). |

### Self-service roles

- **Status:** Implemented
- **Provided by:** `bot.py`
- **What it does:** Admins choose which roles members may self-assign (`!selfrole add/remove/list`); `!roles` posts a role-picker menu where selecting a role toggles it. Founder/Administrator/moderator/bot/Member/Newcomer-style roles and anything with staff permissions are refused.
- **Required Discord permissions:** Manage Roles; ATLAS’s top role must sit above the self-roles.
- **Configuration:** `!selfrole add @Role`.
- **Works independently?** Independent. (NOVA offers a richer role-menu system.)

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!selfrole` | `!selfroleset` | `[action] [role_text]` | Manage Roles | Manage the self-service role list (`add`, `remove`, `list`). |
| `!roles` | `!selfroles` | `[action] [role_text]` | Everyone | Post/refresh the clickable role-picker, or list self-roles. |

### Moderation

- **Status:** Implemented
- **Provided by:** `bot.py`, `atlas_ext/moderation.py`
- **What it does:** Warnings, timeouts, kick/ban (kick/ban restricted to the configured owner), softban, purge, slowmode, voice mute, member `!report` (applies a 10-minute timeout and records it; the member can request a review with a `LIFT <token>` DM), numbered cases with a mod-log, private moderator notes, offender history and a human review queue. Nothing is punished automatically.
- **Required Discord permissions:** Kick Members, Ban Members, Moderate Members, Manage Messages, Manage Channels, Mute Members (per command); role hierarchy is checked.
- **Configuration:** `!config moderation` (`review_channel`, `dm_on_action`, `appeal_info`, `staff_roles`). Set `MODERATION_OWNER_ID` in `.env` to enable `!kick`/`!ban`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!warn` | — | `<member> [reason]` | Moderate Members / Admin | Warn a member (recorded). |
| `!warnings` | — | `<member>` | Moderate Members / Admin | Show a member's warnings. |
| `!clearwarnings` | — | `<member>` | Moderate Members / Admin | Clear a member's warnings. |
| `!kick` | — | `<member> [reason]` | Configured owner only | Kick a member (configured owner only). |
| `!ban` | — | `<member> [reason]` | Configured owner only | Ban a member (configured owner only). |
| `!unban` | — | `<user_id>` | Everyone | Unban by user ID. |
| `!timeout` | — | `<member> <duration> [reason]` | Moderate Members / Admin, Moderate Members | Time a member out (e.g. `10m`, `2h`, `1d`). |
| `!untimeout` | — | `<member> [reason]` | Moderate Members / Admin, Moderate Members | Remove a timeout. |
| `!purge` | — | `<count>` | Manage Messages (or admin) | Delete recent messages. |
| `!slowmode` | — | `<seconds>` | Manage Channels (or admin) | Set channel slowmode in seconds. |
| `!report` | `!bullyreport` | `<member> [details]` | Moderate Members | Any member may report; ATLAS applies and records the report timeout. |
| `!forgive` | — | `<member> [reason]` | Moderate Members / Admin, Moderate Members | Lift a report timeout. |
| `!voicemute` | `!vmute`, `!servermute` | `<member>` | Mute Members | Server-mute a member in voice. |
| `!voiceunmute` | `!vunmute`, `!serverunmute` | `<member>` | Mute Members | Remove a server voice mute. |
| `!case` | — | `<number>` | Moderator perms | Show one moderation case. |
| `!reason` | — | `<number> <text>` | Moderator perms | Update the reason on a case. |
| `!cases` | — | `[member]` | Moderator perms | List recent cases (optionally for one member). |
| `!note` | — | `<member> <text>` | Moderator perms | Add a private moderator note (the member is not told). |
| `!notes` | — | `<member>` | Moderator perms |  |
| `!delnote` | — | `<note_id>` | Moderator perms |  |
| `!modhistory` | `!history`, `!offender` | `<member>` | Moderator perms | Repeat-offender summary: warnings, cases, notes and review items. |
| `!softban` | — | `<member> [reason]` | Ban Members | Ban + immediate unban to clear a member's last day of messages (same owner restriction as !ban). |
| `!reviewqueue` | `!rq` | `[status]` | Moderator perms | Items waiting for a human decision (automod flags, reports, verification). |
| `!rqclose` | — | `<item_id> [status]` | Moderator perms |  |

### Automod

- **Status:** Implemented
- **Provided by:** `atlas_ext/automod.py`
- **What it does:** Blocked words, spam, mention spam, duplicate messages, links (with allow-list) and invites. It only deletes the offending message, posts a short notice, logs it and adds a staff review item. Staff, bots and exempt roles/channels are never touched; an optional short timeout for floods is OFF by default.
- **Required Discord permissions:** Manage Messages.
- **Configuration:** `!config automod` (`blocked_words`, `spam`, `spam_count`, `spam_seconds`, `mention_limit`, `mentions`, `duplicate_count`, `duplicates`, `links`, `allowed_domains`, `invites`, `exempt_channels`, `exempt_roles`, `timeout_floods`). Off until enabled.
- **Works independently?** Independent; uses the review queue from Moderation if enabled.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!automod` | — | — | Manage Server | Automod settings (opens the config panel). |
| `!automod words` | — | — | Everyone |  |
| `!automod words add` | — | `<words>` | Everyone | Add comma-separated words/phrases. |
| `!automod words remove` | — | `<words>` | Everyone |  |
| `!automod test` | — | `<text>` | Everyone | Show which rule (if any) a sample message would trigger. Nothing is deleted. |

### Verification & anti-raid

- **Status:** Implemented
- **Provided by:** `atlas_ext/verification.py`
- **What it does:** Button verification with optional rules acceptance, waiting period, minimum account age with manual review, and join-spike alerts. It only ever ADDS the verified role (never removes roles or edits channel permissions) and alerts staff instead of banning. Run `!verify grantall` before restricting channels to the verified role.
- **Required Discord permissions:** Manage Roles (ATLAS’s role above the verified role); Server Members Intent.
- **Configuration:** `!config verification` (`verified_role`, `panel_channel`, `require_rules`, `wait_minutes`, `min_account_days`, `spike_joins`, `spike_seconds`, `alert_channel`, `alert_role`); `!verify panel`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!verify` | — | — | Everyone | Verification tools. Staff: panel, grantall, approve, deny, stats. |
| `!verify panel` | — | `[channel]` | Manage Server | Post the verification button. |
| `!verify grantall` | — | — | Manage Server | Give the verified role to every current human member (lock-out protection). |
| `!verify approve` | — | `<member>` | Moderator perms | Manually verify a member. |
| `!verify deny` | — | `<member> [reason]` | Moderator perms | Record a denied verification (the member simply stays unverified; nothing else happens). |
| `!verify stats` | — | `[days]` | Moderator perms |  |

### Tickets

- **Status:** Implemented
- **Provided by:** `atlas_ext/tickets.py`
- **What it does:** Private support-ticket channels with categories, claiming, priority, transfer, transcripts (to the staff log channel and the opener by DM), inactivity reminders, feedback and statistics.
- **Required Discord permissions:** Manage Channels (and permission to set channel overwrites), Send Messages.
- **Configuration:** `!config tickets` (`category`, `staff_roles`, `log_channel`, `max_open`, `ping_staff`, `inactivity_hours`, `dm_transcript`, `ask_feedback`); `!ticketpanel`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!ticketpanel` | — | `[channel]` | Manage Server | Post the 'Open a ticket' button. |
| `!ticket` | — | — | Everyone | Ticket tools (use inside a ticket channel). |
| `!ticket claim` | — | — | Everyone |  |
| `!ticket transfer` | `!ticket assign` | `<staff_member>` | Everyone |  |
| `!ticket priority` | — | `<level>` | Everyone |  |
| `!ticket close` | — | — | Everyone |  |
| `!ticket reopen` | — | — | Everyone |  |
| `!ticket add` | — | `<member>` | Everyone |  |
| `!ticket remove` | — | `<member>` | Everyone |  |
| `!ticket transcript` | — | — | Everyone |  |
| `!ticket stats` | — | `[days]` | Everyone |  |
| `!ticket archive` | — | — | Everyone | List closed tickets (staff). |
| `!ticket category` | — | — | Everyone |  |
| `!ticket category add` | — | `<emoji> <rest>` | Manage Server |  |
| `!ticket category remove` | — | `<name>` | Manage Server |  |

### Suggestions, feedback & private reports

- **Status:** Implemented
- **Provided by:** `atlas_ext/feedback.py`
- **What it does:** Named or anonymous suggestions with voting, staff statuses, duplicate detection; `!featurerequest` and `!bugreport`; private member reports (`/modreport` and the “Report message” message context menu) that go only to the staff review queue.
- **Required Discord permissions:** Send Messages, Embed Links, Add Reactions; Create Public Threads (optional).
- **Configuration:** `!config feedback` (`suggestion_channel`, `allow_anonymous`, `categories`, `duplicate_threshold`, `reports`, `bug_channel`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!suggest` | — | `<text>` | Everyone | Post a suggestion. `!suggest anon <idea>` hides your name. Optional `[category]` at the start. |
| `/suggest` | — | `<idea> [anonymous] [category]` | Everyone | Post a suggestion for the server |
| `!featurerequest` | `!fr` | `<idea>` | Everyone | Request a feature (posted as a suggestion in the 'feature' category). |
| `!bugreport` | `!reportproblem` | `<problem>` | Everyone | Report a problem with the server or bots to staff. |
| `!suggestion` | — | `[number]` | Everyone | Show a suggestion. Staff: approve/reject/implement/consider/duplicate <number> [response]. |
| `!suggestion approve` | — | `<number> [response]` | Everyone |  |
| `!suggestion reject` | — | `<number> [response]` | Everyone |  |
| `!suggestion implement` | — | `<number> [response]` | Everyone |  |
| `!suggestion consider` | — | `<number> [response]` | Everyone |  |
| `!suggestion duplicate` | — | `<number> [response]` | Everyone |  |
| `!suggestions` | — | `[status]` | Everyone | Browse suggestions by status (open, approved, rejected, implemented, considering, all). |
| `!suggestionstats` | — | — | Everyone |  |
| `/modreport` | — | `<reason> [member]` | Everyone | Privately report a member or problem to staff |
| `!myreports` | — | — | Everyone | Status of reports you've filed here. |
| Right-click message → Apps → **Report message** | — | — | Everyone | Right-click a message → Apps |

### Polls

- **Status:** Implemented
- **Provided by:** `bot.py`, `atlas_ext/polls.py`
- **What it does:** Button polls (yes/no or up to 10 options, single/multiple choice, optional anonymity, expiry, vote changes, duplicate-vote prevention, results announcements, history) plus the original quick reaction poll `!poll <question>`.
- **Required Discord permissions:** Send Messages, Embed Links.
- **Configuration:** `!config polls` (`default_hours`, `results_channel`, `who_can_create`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!poll` · `/poll` | — | `<question>` | Everyone | Create a simple yes/no reaction poll |
| `!polls` | — | — | Everyone | Button polls. `!polls new Question \| A \| B [\| C...] [--anon] [--multi] [--for 2h]` |
| `!polls new` | `!polls create` | `<spec>` | Everyone |  |
| `!polls yesno` | — | `<spec>` | Everyone |  |
| `/pollnew` | — | `<question> [options] [duration] [anonymous] [multiple]` | Everyone | Create a button poll |
| `!polls end` | `!polls close` | `<poll_id>` | Everyone |  |
| `!polls results` | — | `<poll_id>` | Everyone |  |
| `!polls list` | `!polls history` | `[status]` | Everyone |  |

### Events

- **Status:** Implemented
- **Provided by:** `atlas_ext/events.py`
- **What it does:** Community events with RSVP buttons, timezone-aware reminders, editing, cancellation and history. State lives in the database so RSVPs and reminders survive restarts; reminders are claimed atomically (never sent twice).
- **Required Discord permissions:** Send Messages, Embed Links.
- **Configuration:** `!config events` (`channel`, `timezone`, `reminders`, `dm_reminders`, `who_can_create`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!event` | `!events` | — | Everyone | Events: create, list, past, info, edit, cancel, stats. |
| `!event create` | `!event new` | `<spec>` | Everyone | `!event create Title \| when \| description` — when: `2026-12-31 20:00`, `tomorrow 7pm`, `in 3h`. |
| `/eventcreate` | — | `<title> <when> [description] [channel]` | Everyone | Create a community event with RSVP buttons |
| `!event list` | `!event upcoming` | — | Everyone |  |
| `!event past` | `!event history` | — | Everyone |  |
| `!event info` | — | `<event_id>` | Everyone |  |
| `!event edit` | — | `<event_id> <field> <value>` | Everyone | `!event edit <id> title\|when\|description <value>` |
| `!event cancel` | — | `<event_id> [reason]` | Everyone |  |
| `!event stats` | — | — | Everyone |  |

### Reminders

- **Status:** Implemented
- **Provided by:** `atlas_ext/reminders.py`
- **What it does:** Persistent personal, server (staff) and recurring reminders, timezone-aware and restart-safe. `!remind 10 text` means “in 10 minutes”.
- **Required Discord permissions:** Send Messages (and DMs allowed for personal reminders).
- **Configuration:** `!config reminders` (`server_reminders`, `default_dm`); members set `!timezone`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!remind` · `/remind` | — | `<when> <text>` | Everyone | Set a reminder (e.g. 10, 2h30m, tomorrow 9am) |
| `!remindevery` | `!recurring` | `<interval> <text>` | Everyone | Recurring reminder: `!remindevery 1d drink water` (minimum 10 minutes). |
| `!serverremind` | `!sremind` | `<channel> <spec>` | Moderator perms | Staff: `!serverremind #channel <when> <text>` or `!serverremind #channel every 1d <text>`. |
| `!reminders` | `!myreminders` | — | Everyone | Your pending reminders (and this server's, for staff). |
| `!reminder` | — | — | Everyone |  |
| `!reminder cancel` | `!reminder delete`, `!reminder remove` | `<rid>` | Everyone |  |
| `!reminder edit` | — | `<rid> <text>` | Everyone |  |
| `!reminder move` | `!reminder reschedule` | `<rid> <when>` | Everyone |  |

### Community engagement

- **Status:** Implemented
- **Provided by:** `atlas_ext/engagement.py`
- **What it does:** Daily question, conversation starters, message milestones, join anniversaries, recognition (kudos), featured messages, weekly highlights and community challenges. Each piece is separately switchable; the module is off by default.
- **Required Discord permissions:** Send Messages, Embed Links; Server Members Intent for anniversaries.
- **Configuration:** `!config engagement` (`daily_question`, `question_channel`, `question_time`, `timezone`, `question_thread`, `anniversaries`, `milestones`, `highlights_channel`, `weekly_highlights`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!question` | `!qotd`, `!randomquestion` | — | Everyone | A random conversation question. |
| `!topic` | `!starter` | — | Everyone | A conversation starter. |
| `!questionadd` | — | `<text>` | Manage Server | Add a custom daily question. |
| `!recognize` | `!recognise`, `!kudos` | `<member> <reason>` | Everyone | Publicly recognise a member for something great. |
| `!feature` | — | `<link>` | Moderator perms | Feature a message in the highlights channel (staff). Use the message link. |
| `!highlights` | — | — | Everyone | Recently featured messages and recognitions. |
| `!challenge` | — | — | Everyone | Community challenge: view, join. Staff: set, end. |
| `!challenge set` | — | `<spec>` | Moderator perms |  |
| `!challenge join` | `!challenge done`, `!challenge submit` | `[note]` | Everyone | Join your voice channel (TTS + 24/7 presence). |
| `!challenge end` | — | — | Moderator perms |  |

### Statistics & analytics

- **Status:** Implemented
- **Provided by:** `atlas_stats.py`, `atlas_stats_core.py`, `atlas_ext/statsx.py`
- **What it does:** Tracks messages, replies, voice time, reactions and games (Presence Intent optional) and renders reports with charts: personal (`!kavstat`), server leaderboards, voice/message/channel/game views and `/stats` commands, plus join/leave tracking and scheduled weekly/monthly reports. History from before tracking started can only be partly recovered (messages only, via `!kavstatbackfill`, last 30 days).
- **Required Discord permissions:** Read Message History; Presence Intent (optional, game stats); Server Members Intent.
- **Configuration:** `!config statistics` (`track_joins`, `report_channel`, `weekly_report`, `monthly_report`, `timezone`). Data is stored locally in `atlas_stats.json` and `atlas.db`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!kavstat` | `!atlas_stats` | `[member] [period]` | Everyone | Personal activity report with graph. |
| `!kavstatall` | `!kavboard`, `!kavlb` | `[period]` | Everyone | Server leaderboard overview. |
| `!kavstatvc` | — | `[period]` | Everyone |  |
| `!kavstatmsg` | — | `[period]` | Everyone |  |
| `!kavstatchannels` | — | `[period]` | Everyone |  |
| `!kavstatgames` | — | `[period]` | Everyone |  |
| `!kavstatbackfill` | — | — | Everyone | Import the last 30 days of messages from before tracking started (Manage Server). |
| `!stats` · `/stats` | — | `[period]` | Everyone | Server activity statistics |
| `!stats daily` | — | — | Everyone | Last 24 hours |
| `!stats weekly` | — | — | Everyone | Last 7 days |
| `!stats monthly` | — | — | Everyone | Last 30 days |
| `!stats alltime` | — | — | Everyone | Everything tracked |
| `!stats member` | — | `[member] [period]` | Everyone | One member's activity report |
| `!stats channel` | — | `[channel] [period]` | Everyone | Channel activity ranking, or one channel's numbers |
| `!stats voice` | — | `[period]` | Everyone | Voice participation ranking |
| `!stats joins` | — | `[days]` | Everyone | Join and leave trends |
| `!stats report` | — | `[period]` | Everyone | Full community report (admins) |

### Games & leaderboards

- **Status:** Implemented
- **Provided by:** `atlas_ext/games.py`, `atlas_ext/game_data.py`
- **What it does:** Button games with turn tracking, timers, scores, leaderboards and history: tic-tac-toe, Connect Four, rock-paper-scissors duel, trivia (solo/team/daily, custom questions), number guessing, hangman, emoji guess, riddles, memory, reaction race, word chain. No betting or currency of any kind.
- **Required Discord permissions:** Send Messages, Embed Links, Add Reactions.
- **Configuration:** `!config games` (`trivia_seconds`, `game_channels`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!ttt` | `!tictactoe` | `<opponent>` | Everyone | Tic-tac-toe against another member. |
| `!c4` | `!connect4`, `!connectfour` | `<opponent>` | Everyone | Connect Four against another member. |
| `!rpsduel` | `!rpsvs` | `<opponent>` | Everyone | Rock-paper-scissors against another member (picks stay secret). |
| `!trivia` | — | `[category] [difficulty]` | Everyone | Multiple-choice trivia. `!trivia [science\|geography\|history\|music\|gaming\|general] [easy\|medium\|hard]` |
| `!triviateam` | `!triviaparty`, `!quiz` | `[rounds] [teams]` | Everyone | Channel trivia for everyone. `!triviateam 5` (free-for-all) or `!triviateam 5 Red, Blue` (team mode with a lobby). |
| `!dailyquiz` | — | — | Everyone | Today's 5-question quiz — same questions for everyone, one attempt per day. |
| `!triviaadd` | — | `<spec>` | Manage Server | Add a custom question: `category \| easy\|medium\|hard \| question \| correct \| wrong1 \| wrong2 \| wrong3 \| explanation` |
| `!triviaremove` | — | `[question_id]` | Manage Server | List custom questions, or remove one by ID. |
| `!guessnumber` | `!numberguess`, `!gn` | `[maximum]` | Everyone | Guess my number (7 tries, hints after each guess). |
| `!hangman` | `!wordguess` | — | Everyone | Guess the word letter by letter (6 lives). Type single letters or the whole word. |
| `!emojiguess` | `!emojiquiz` | — | Everyone | Guess the movie/phrase from emojis — first correct answer in the channel wins. |
| `!riddle` | — | — | Everyone | A riddle with a reveal button. |
| `!memory` | — | `[length]` | Everyone | Memorise the emoji sequence, then repeat it with the buttons. |
| `!reaction` | `!reactiontest`, `!fastest` | — | Everyone | First to press the button when it turns green wins. |
| `!wordchain` | — | — | Everyone | Word chain: each word must start with the last letter of the previous one. `!wordchain start\|stop` |
| `!wordchain start` | — | — | Everyone |  |
| `!wordchain stop` | — | — | Everyone |  |
| `!gamesboard` | `!gameslb`, `!gameleaderboard` | `[game]` | Everyone | Game leaderboard (all games, or one: tictactoe, connect4, rps, trivia, hangman, guessnumber, memory, reaction, wordchain, emojiguess, dailyquiz). |
| `!gamehistory` | `!matches` | `[member]` | Everyone | Recent matches (optionally for one member). |

### Announcements & creator feeds (integrations)

- **Status:** Implemented; Twitch is an Optional integration
- **Provided by:** `atlas_ext/announce.py`
- **What it does:** Scheduled/recurring announcements, one-off formatted announcements, and creator feeds: RSS/Atom and YouTube uploads (via YouTube’s public RSS feed, no key) and optional Twitch live alerts. New feeds are “seeded” so old posts are not flooded.
- **Required Discord permissions:** Send Messages, Embed Links.
- **Configuration:** `!config announcements` / `!config feeds`. Twitch needs `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` in `.env`.
- **Works independently?** Independent; Twitch needs a free Twitch developer app.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!announce` | `!announcement` | — | Manage Server | Scheduled announcements. Subcommands: add, list, remove, toggle, test, post. |
| `!announce add` | — | `<channel> <spec>` | Everyone |  |
| `!announce list` | — | — | Everyone |  |
| `!announce remove` | `!announce delete` | `<aid>` | Everyone |  |
| `!announce toggle` | — | `<aid>` | Everyone |  |
| `!announce test` | — | `<aid>` | Everyone |  |
| `!announce post` | — | `<channel> <text>` | Everyone | Post a formatted announcement now: `!announce post #ch Title \| body [--ping @role]` |
| `!feeds` | `!feed` | — | Manage Server | Creator feeds: add youtube/rss/twitch, list, remove, health. |
| `!feeds add` | — | `<kind> <channel> <source> [label]` | Everyone |  |
| `!feeds remove` | — | `<fid>` | Everyone |  |
| `!feeds ping` | — | `<fid> [role]` | Everyone | Show latency. |
| `!feeds template` | — | `<fid> [template]` | Everyone |  |
| `!feeds health` | — | — | Everyone |  |

### Logging & audit trail

- **Status:** Implemented
- **Provided by:** `atlas_ext/logs.py`
- **What it does:** Posts member joins/leaves, nickname/role changes, message deletes/edits, channel/role/permission changes and voice events to private staff channels. Message text is NOT logged unless you enable “Include message text”. (No commands; configuration only.)
- **Required Discord permissions:** View Audit Log (recommended); Send Messages in the log channels.
- **Configuration:** `!config logging` (`log_channel`, `member_channel`, `message_channel`, `mod_channel`, `server_channel`, `voice_channel`, `joins`, `leaves`, `nicknames`, `roles`, `message_delete`, `message_edit`, `message_content`, `channels`, `permissions`, `ignored_channels`, `voice`).
- **Works independently?** Independent.

_No commands; works through events and configuration only._

### Starboard, message of the day & bookmarks

- **Status:** Implemented
- **Provided by:** `atlas_ext/starboard.py`
- **What it does:** Highlights messages that reach a star-reaction threshold (age-restricted messages never go to a non-age-restricted starboard), featured message of the day, and private bookmarks sent only to the person who saved them by DM (`!save` or the “Bookmark message” context menu).
- **Required Discord permissions:** Add Reactions, Send Messages, Embed Links; ability to DM the member for bookmarks.
- **Configuration:** `!config starboard` (`channel`, `emoji`, `self_star`, `threshold`, `remove_below`, `excluded_channels`, `excluded_roles`, `message_of_day`, `timezone`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!save` | `!bookmark` | `[link] [note]` | Everyone | Bookmark a message (reply to it, or give its link). The copy is DM'd only to you. |
| `!bookmarks` | — | — | Everyone | Your saved messages (sent by DM). |
| `!unbookmark` | — | `<bookmark_id>` | Everyone |  |
| `!starboardtop` | `!startop` | — | Everyone |  |
| Right-click message → Apps → **Bookmark message** | — | — | Everyone | Right-click a message → Apps |

### Rules

- **Status:** Implemented
- **Provided by:** `bot.py`, `atlas_ext/rules.py`
- **What it does:** `!postrules` posts a generic community-rules text; the Rules module adds versioned rules with an accept button, lookup, staff editing, publish/update announcements and acceptance statistics.
- **Required Discord permissions:** Send Messages, Embed Links; Manage Roles if an acceptance role is set.
- **Configuration:** `!config rules` (`channel`, `announce_updates`, `announce_channel`, `remind_new`, `acceptance_role`). Edit the default text in `bot.py` (`COMMUNITY_RULES`) or with `!rulesedit`.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!postrules` | — | — | Everyone | Post the community rules text (owner/admin). |
| `!rules` | — | — | Everyone | Show the current rules (with an accept button). |
| `!rules draft` | — | — | Manage Server |  |
| `!rules history` | — | — | Manage Server |  |
| `!rule` | — | `<number>` | Everyone | Look up a single rule by number. |
| `!rulesedit` | — | `[text]` | Manage Server | Edit the rules: `!rulesedit` opens an editor (button), or pass the full text with one rule per line. |
| `!rulespublish` | — | — | Manage Server | Publish the draft as a new rules version (members can re-accept). |
| `!rulesstatus` | — | — | Moderator perms | How many members accepted the current rules. |

### Embed builder

- **Status:** Implemented
- **Provided by:** `atlas_ext/embeds.py`
- **What it does:** Admin-only embed builder with modals, live preview, validation and a publish confirmation; can edit an existing ATLAS embed by link. Posts as the bot (no webhooks).
- **Required Discord permissions:** Send Messages, Embed Links.
- **Configuration:** None.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!embed` | `!embedbuilder` | `[channel]` | Manage Server | Open the embed builder (Manage Server). |
| `!embededit` | — | `<link>` | Manage Server | Edit an embed ATLAS posted earlier (give its message link). |

### Utilities & help

- **Status:** Implemented
- **Provided by:** `bot.py`, `atlas_ext/utility.py`
- **What it does:** Help guide (`!help`, paginated), server/user info, avatar/banner/profile, role list, emoji tools, message quotes, fun facts, command search, custom prefix, latency, AFK status (replies to mentions and clears when you chat), random chooser, and the bot-owner shutdown phrase.
- **Required Discord permissions:** Send Messages, Embed Links.
- **Configuration:** `!prefix` to change the prefix. `BOT_OWNER_IDS` in `.env` for the shutdown command.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!afk` · `/afk` | — | `[reason]` | Everyone | Set an AFK status message |
| `!help` · `/help` | `!commands`, `!h` | `[category]` | Everyone | Show ATLAS's command guide |
| `!prefix` | — | `<new_prefix>` | Moderate Members / Admin | Set this server's command prefix. |
| `!resetprefix` | — | — | Moderate Members / Admin | Restore the default prefixes. |
| `!ping` · `/ping` | — | — | Everyone | Check ATLAS's Discord latency |
| `!choose` · `/choose` | — | `<options>` | Everyone | Randomly choose between options separated by \| |
| `!okay` | — | `[phrase]` | Everyone | Bot owners only: `okay bye good night` saves data and shuts ATLAS down. |
| `!serverinfo` · `/serverinfo` | — | — | Everyone | Display information about this server |
| `!userinfo` · `/userinfo` | — | `[member]` | Everyone | Display information about a server member |
| `!avatar` · `/avatar` | — | `[member]` | Everyone | View a member's avatar |
| `!banner` · `/banner` | — | `[member]` | Everyone | View a member's profile banner |
| `!profile` · `/profile` | — | `[member]` | Everyone | A member's profile card |
| `!roleslist` | `!myroles` | `[member]` | Everyone | List a member's roles (highest first). |
| `!emoji` | `!enlarge`, `!bigemoji` | `<emoji>` | Everyone | Show a custom emoji large with its info. |
| `!emojis` | `!emojilist` | — | Everyone | List this server's custom emojis. |
| `!quote` | — | `<link>` | Everyone | Quote a message by its link (only from channels you can read). |
| `!funfact` | `!fact` | — | Everyone |  |
| `!commandsearch` | `!findcommand`, `!searchcmd` | `<word>` | Everyone | Search ATLAS's commands by name or description. |

### Voice & TTS

- **Status:** **Known issue** (see docs/TROUBLESHOOTING.md)
- **Provided by:** `bot.py`, `atlas_ext/voice.py`, `common/voice.py`
- **What it does:** ATLAS can stay in a voice channel 24/7 (auto-rejoin, with a fix that no longer mistakes a network reconnect for a moderator removal) and read messages from a configured text channel aloud using the local Piper engine. Queue controls, volume, diagnostics. **TTS status:** a gate bug that silently skipped sentences starting with `k`, `?` or `.` was fixed in this release (unit-tested), but end-to-end speech in Discord has NOT been verified by the release process; the maintainer reports an outstanding TTS issue.
- **Required Discord permissions:** Connect, Speak (View Audit Log recommended).
- **Configuration:** `!ttschannel #channel`; `!config voice` (`volume`, `dj_role`). Needs FFmpeg and the Piper voice (`setup.bat`).
- **Works independently?** Needs `piper-tts`, FFmpeg and `discord.py[voice]`.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!ttschannel` | — | `[channel]` | Manage Guild | Choose the TTS text channel (Manage Server). |
| `!ttsdisable` | — | — | Manage Guild | Turn TTS off for this server. |
| `!join` | — | — | Connect, Speak | Join your voice channel (TTS + 24/7 presence). |
| `!leave` | `!disconnect` | — | Everyone | Leave voice and pause the 24/7 rejoin. |
| `!vstatus` | `!voicestatus` | — | Everyone | ATLAS's voice connection, listeners, playback and queue. |
| `!vdiag` | `!voicediag` | — | Everyone | Detailed voice diagnostics: permissions, libraries, recent errors. |
| `!queue` | `!ttsqueue` | — | Everyone | Show the TTS queue. |
| `!skip` | — | — | Everyone | Skip the line being spoken. |
| `!stop` | `!ttsstop` | — | Everyone | Stop speaking and clear the queue (ATLAS stays in voice). |
| `!pause` | — | — | Everyone |  |
| `!resume` | — | — | Everyone |  |
| `!volume` | `!vol` | `[percent]` | Everyone | Show or set TTS volume (10–200%). Applies from the next line. |

### Optional local AI (LM Studio)

- **Status:** Optional integration
- **Provided by:** `bot.py`, `ai_approval.py`
- **What it does:** Replies to mentions/`!ai` using a model served by LM Studio on your own machine (`AI_BASE_URL`, `AI_MODEL` in `config.py`); message content is sent only to that local endpoint. `!ai` can hand out a role from an allow-list, `!aidm` and `!aisay` can DM/speak — each action must be approved in a separate Windows console window (Windows only). Off until `!aion`.
- **Required Discord permissions:** Manage Roles for role requests; Connect/Speak for `!aisay`.
- **Configuration:** LM Studio running with the server started; `AI_ASSIGNABLE_ROLE_NAMES` in `config.py`.
- **Works independently?** Requires LM Studio; approval window is Windows-only.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!aion` | — | — | Everyone | Enable local-AI replies for this server. |
| `!aioff` | — | — | Everyone | Disable local-AI replies. |
| `!ai` | — | `<prompt>` | Everyone | Ask the local AI (LM Studio). |
| `!aidm` | — | `<member> <text>` | Everyone | Ask ATLAS to DM a member (needs console approval). |
| `!aisay` | — | `<text>` | Everyone | Speak text in voice (needs console approval). |

### Server admin tools & DM intake

- **Status:** Implemented
- **Provided by:** `bot.py`
- **What it does:** `!rebuild` (DESTRUCTIVE; disabled unless `PRESERVED_CHANNEL_ID` is set; typed confirmation) recreates a generic 3-category layout; `!clearchannel` recreates a channel to wipe history (typed confirmation); `!visibility` DMs an admin a snapshot of recent messages; new DMs sent to the bot are forwarded to `DM_FORWARD_CHANNEL_ID` while it is online (off when unset).
- **Required Discord permissions:** Manage Channels (rebuild/clear); View Channel + Send Messages in the DM-forward channel.
- **Configuration:** `PRESERVED_CHANNEL_ID`, `DM_FORWARD_CHANNEL_ID` in `.env`.
- **Works independently?** Independent. See docs/PRIVACY.md before enabling DM forwarding.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!dmforwardtest` | — | — | Everyone | Test the DM-forward destination channel. |
| `!rebuild` | — | — | Everyone | DESTRUCTIVE: rebuild the server channel layout (disabled unless PRESERVED_CHANNEL_ID is set; typed confirmation). |
| `!clearchannel` | `!clearhistory` | `<channel>` | Everyone | DESTRUCTIVE: recreate a text channel to wipe its history (typed confirmation). |
| `!visibility` | — | — | Everyone | DM the admin a snapshot of recent messages ATLAS can read. |

### Configuration & diagnostics

- **Status:** Implemented
- **Provided by:** `common/diag.py`, `common/config.py`
- **What it does:** Discord-native settings panel (every feature module can be switched on/off independently without deleting data), diagnostics, personal timezone/preferences, configuration export/import and backup history. No shell/eval commands exist; tokens are never shown.
- **Required Discord permissions:** Manage Server for admin commands.
- **Configuration:** none.
- **Works independently?** Yes.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!config` · `/config` | `!settings` | `[module]` | Manage Server | Open the settings panel (Manage Server). |
| `!diag` · `/diag` | `!diagnostics`, `!botinfo` | `[section]` | Manage Server | Diagnostics: `diag`, `diag errors`, `diag perms`, `diag config` (Manage Server). |
| `!timezone` · `/timezone` | `!tz` | `[zone]` | Everyone | Set your timezone for reminders/events. |
| `!prefs` · `/prefs` | `!preferences` | — | Everyone | Your personal notification preferences and opt-outs. |
| `!configexport` | — | — | Manage Server | Export this server's configuration as JSON (no tokens/secrets). |
| `!configimport` | — | — | Manage Server | Import a configuration export (attach the .json); shows a preview first. |
| `!configbackups` | — | `[restore_id]` | Manage Server | List configuration backups or restore one. |

# NOVA

NOVA is the role-and-voice bot (`nova.py`, `nova_ext/`). Internal module names keep their historical `nova_` spelling for compatibility.

### Vibe replies & profile pictures

- **Status:** Implemented
- **Provided by:** `nova.py`
- **What it does:** When ATLAS posts a vibe (message beginning with the sun emoji) in the vibe channel, NOVA replies with the matching answer; `!pfp` shows a profile picture.
- **Required Discord permissions:** Send Messages, Read Message History; Message Content Intent.
- **Configuration:** Both bots in the server; vibe channel set on ATLAS with `!vibechannel`.
- **Works independently?** Requires ATLAS for the vibe replies; `!pfp` is independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!pfp` | — | — | Everyone | Show a profile picture: `pfp [@member\|ID]`. |

### Colour roles

- **Status:** Implemented
- **Provided by:** `nova.py`
- **What it does:** `!colours` posts a picker (dropdown of presets + custom RGB/hex); NOVA creates and assigns a personal colour role. `!mclearrolecolour` (configured owner only, preview + confirmation button) clears role colours.
- **Required Discord permissions:** Manage Roles; NOVA’s role above the colour roles.
- **Configuration:** `MODERATION_OWNER_ID` for the clear command.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!colours` | — | — | Everyone | Post the colour-role picker (dropdown + custom RGB/hex). |
| `!colors` | — | — | Everyone | Alias spelling of `colours`. |
| `!mclearrolecolour` | — | — | Everyone | Configured owner only: preview and clear role colours (confirmation button). |
| `!mclearrolecolor` | — | — | Everyone | Alias spelling of `mclearrolecolour`. |

### Role menus

- **Status:** Implemented
- **Provided by:** `nova_ext/roles.py`
- **What it does:** Button/dropdown role menus (toggle, unique, add-only modes), required roles, preview/validate/post/refresh. Roles with staff/administrative permissions can never be put in menus. Every action checks Discord permissions and hierarchy for both the bot and the member.
- **Required Discord permissions:** Manage Roles (user and bot).
- **Configuration:** `!mconfig roles` (`log_channel`, `track_history`, `cleanup_colour_roles`, `menu_cooldown`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mrolemenu` | `!mrm` | — | Manage Roles | Role menus. Subcommands: create, add, remove, mode, require, preview, validate, post, refresh, list, delete. |
| `!mrolemenu create` | — | `<style> <text>` | Everyone |  |
| `!mrolemenu add` | — | `<menu_id> <role> [rest]` | Everyone |  |
| `!mrolemenu remove` | — | `<menu_id> <role>` | Everyone |  |
| `!mrolemenu mode` | — | `<menu_id> <mode>` | Everyone |  |
| `!mrolemenu require` | — | `<menu_id> <role>` | Everyone |  |
| `!mrolemenu preview` | — | `<menu_id>` | Everyone |  |
| `!mrolemenu validate` | — | `<menu_id>` | Everyone |  |
| `!mrolemenu post` | — | `<menu_id> [channel]` | Everyone |  |
| `!mrolemenu refresh` | — | `<menu_id>` | Everyone |  |
| `!mrolemenu list` | — | — | Everyone |  |
| `!mrolemenu delete` | — | `<menu_id>` | Everyone |  |

### Reaction roles

- **Status:** Implemented
- **Provided by:** `nova_ext/roles.py`
- **What it does:** Assign a role when members react with an emoji on a message link.
- **Required Discord permissions:** Manage Roles; Add Reactions; Read Message History.
- **Configuration:** None.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mreactionrole` | `!mrr` | — | Manage Roles | Reaction roles: add <message link> <emoji> @Role · remove <message link> <emoji> · list |
| `!mreactionrole add` | — | `<link> <emoji> <role>` | Everyone |  |
| `!mreactionrole remove` | — | `<link> <emoji>` | Everyone |  |

### Temporary roles

- **Status:** Implemented
- **Provided by:** `nova_ext/roles.py`
- **What it does:** Give a role for a duration; it is removed automatically when it expires; list/cancel; clean up expired/colour roles.
- **Required Discord permissions:** Manage Roles; Server Members Intent recommended.
- **Configuration:** None.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mtemprole` | — | `<member> <role> <duration> [reason]` | Manage Roles | Give a role that is removed automatically: `!mtemprole @member @Role 2d [reason]` |
| `!mtemproles` | — | — | Manage Roles | Active temporary roles. |
| `!mtemprolecancel` | — | `<temp_id> [keep]` | Manage Roles | Cancel a temporary role: `!mtemprolecancel <id> [keep\|remove]` (keep = make it permanent). |
| `!mrolecleanup` | — | — | Manage Roles | Preview empty roles. Only NOVA's own unused colour roles can be deleted here (with confirmation). |

### Role templates & bulk role management

- **Status:** Implemented
- **Provided by:** `nova_ext/roles.py`
- **What it does:** Save/apply role templates and bulk add/remove a role for a target group.
- **Required Discord permissions:** Manage Roles; Server Members Intent.
- **Configuration:** None.
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mroletemplate` | `!mrt` | — | Manage Roles | Role templates: list, show <name>, save <name> @roles..., apply <name>, delete <name> |
| `!mroletemplate show` | — | `<name>` | Everyone |  |
| `!mroletemplate save` | — | `<name> <roles>` | Everyone |  |
| `!mroletemplate apply` | — | `<name>` | Everyone |  |
| `!mroletemplate delete` | — | `<name>` | Everyone |  |
| `!mbulkrole` | — | `<action> <role> [target]` | Manage Roles | Bulk add/remove a role with preview + confirmation. |

### Role & permission diagnostics, role history

- **Status:** Implemented
- **Provided by:** `nova_ext/roles.py`
- **What it does:** Role info/permissions/comparison, hierarchy view, “can this member get this role?”, role history log, bot role/permission checks and channel permission views.
- **Required Discord permissions:** Manage Roles for history; otherwise standard.
- **Configuration:** `!mconfig roles` (`track_history`, `log_channel`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mroleinfo` | — | `<role>` | Everyone | Details about a role. |
| `!mroleperms` | — | `<role>` | Everyone | Every permission a role grants. |
| `!mpermcompare` | — | `<a> <b>` | Everyone | Compare two roles' permissions. |
| `!mrolehierarchy` | `!mhierarchy` | — | Everyone | Role order with what NOVA can manage. |
| `!mroleeligible` | `!mcanget` | `<member> <role>` | Everyone | Explain whether a member can get a role (via NOVA's menus / staff tools). |
| `!mrolehistory` | — | `<target>` | Manage Roles | Role change history for a member or a role. |
| `!mbotroles` | `!mpermcheck` | `[channel]` | Everyone | NOVA's highest role, permissions and channel-level conflicts (read-only). |
| `!mchannelperms` | — | `<channel> [target]` | Everyone | Effective permissions for a member or role in a channel (read-only). |

### Temporary voice channels

- **Status:** Implemented
- **Provided by:** `nova_ext/tempvoice.py`
- **What it does:** Join-to-create rooms: joining a hub channel creates a personal voice room with owner controls (name, limit, lock/hide, claim, transfer, panel) that disappears when empty. NOVA only deletes rooms it created itself.
- **Required Discord permissions:** Manage Channels, Move Members, Connect.
- **Configuration:** `!mvc setup <hub> [category] [template]`; `!mconfig tempvoice` (`max_rooms`, `cooldown`, `panel`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mvc` | `!mtempvc` | — | Everyone | Temporary rooms. In your room: name, limit, lock, unlock, hide, show, transfer, claim, panel. Admin: setup, hubs, removehub. |
| `!mvc name` | `!mvc rename` | `<name>` | Everyone |  |
| `!mvc limit` | — | `<limit>` | Everyone |  |
| `!mvc lock` | — | — | Everyone |  |
| `!mvc unlock` | — | — | Everyone |  |
| `!mvc hide` | — | — | Everyone |  |
| `!mvc show` | `!mvc unhide` | — | Everyone |  |
| `!mvc claim` | — | — | Everyone |  |
| `!mvc transfer` | — | `<member>` | Everyone |  |
| `!mvc panel` | — | — | Everyone |  |
| `!mvc setup` | — | `<hub> [category] [template]` | Manage Channels |  |
| `!mvc hubs` | — | — | Manage Channels |  |
| `!mvc removehub` | — | `<hub>` | Manage Channels |  |

### Channel & message tools

- **Status:** Implemented
- **Provided by:** `nova_ext/channels.py`, `nova.py`
- **What it does:** Channel info/topic/slowmode, timed lock/unlock, cleanup with preview and confirmation, threads (create/archive/auto-thread), forum posts, pins, search (results by DM, only channels you can read), jump links, sticky messages, and the `/clear` slash command. NOVA never deletes channels here.
- **Required Discord permissions:** Manage Channels, Manage Messages, Manage Threads, Create Public Threads (per command).
- **Configuration:** `!mconfig channels` (`sticky_delay`, `slowmode_notice`).
- **Works independently?** Independent.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mchannelinfo` | `!mchinfo` | `[channel]` | Everyone | Information about a channel. |
| `!mtopic` | `!mdescription` | `[channel] [text]` | Manage Channels | Set (or clear) a channel's description/topic. |
| `!mslowmode` | — | `[duration] [channel]` | Manage Channels | Set slowmode: `!mslowmode 10s`, `!mslowmode 2m`, `!mslowmode off`. |
| `!mlock` | — | `[channel] [duration] [reason]` | Manage Channels | Lock a channel for @everyone (staff with their own permissions can still talk). Optional duration: `!mlock #ch 30m`. |
| `!munlock` | — | `[channel]` | Manage Channels | Unlock a channel (restores the previous @everyone settings). |
| `!mcleanup` | `!mpurge` | `[amount] [filters]` | Manage Messages | Preview and delete recent messages. Filters: bots · commands · @member · contains:<text> · links · attachments · humans |
| `!mthread` | — | `[name]` | Everyone | Start a thread (reply to a message to thread it). `!mthread Topic name` |
| `!marchive` | — | `[channel] [inactive]` | Manage Threads | Archive this thread, or `!marchive #channel 7d` to archive that channel's threads inactive for 7 days. |
| `!mautothread` | — | `[channel] [state]` | Manage Channels | Automatically open a thread on every new message in a channel: `!mautothread #channel on\|off` |
| `!mforumpost` | — | `<forum> <text>` | Everyone | Create a forum post: `!mforumpost #forum Title \| body` |
| `!mpins` | — | `[channel]` | Everyone | List pinned messages with jump links. |
| `!msearch` | — | `[channel] <query>` | Everyone | Search recent messages (last 1000) in a channel you can read; results are DM'd to you. |
| `!mjump` | — | `[channel] [message_id]` | Everyone | Jump link to a message by ID, or to the first message of a channel. |
| `!msticky` | — | `[text]` | Manage Messages | Keep a message at the bottom of this channel: `!msticky <text>`, `!msticky off`. |
| `/clear` | — | `[amount] [member] [include_bots] [everything]` | Everyone | NOVA deletes messages in this channel (members' messages by default) |

### Voice & TTS

- **Status:** **Known issue** (see docs/TROUBLESHOOTING.md)
- **Provided by:** `nova.py`, `nova_ext/voice.py`
- **What it does:** 24/7 voice presence (`!mjoin`/`!mleave`), reading `#nova-tts` aloud in Hindi (Devanagari) + English with female Piper voices (Hinglish is transliterated to Devanagari), queue/volume controls, voice diagnostics, participants with session durations, optional join/leave announcements and the server AFK channel. TTS has not been verified end-to-end by the release process.
- **Required Discord permissions:** Connect, Speak, Move Members (AFK).
- **Configuration:** `!mtts`; `!mconfig voice` (`volume`, `announce`, `announce_channel`, `dj_role`). Voices are downloaded on first use (needs internet).
- **Works independently?** Needs `piper-tts`, FFmpeg and `discord.py[voice]`.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mvstatus` | `!mvoicestatus` | — | Everyone | NOVA's voice connection, playback and queue. |
| `!mvdiag` | `!mvoicediag` | — | Everyone | NOVA's voice diagnostics: permissions, libraries, recent errors. |
| `!mqueue` | — | — | Everyone | NOVA's TTS queue. |
| `!mskip` | — | — | Everyone |  |
| `!mstop` | — | — | Everyone |  |
| `!mpause` | — | — | Everyone |  |
| `!mresume` | — | — | Everyone |  |
| `!mvolume` | `!mvol` | `[percent]` | Everyone | Show or set NOVA's TTS volume (10–200%). |
| `!mvoiceinfo` | `!mvcinfo`, `!mwhoisin` | `[channel]` | Everyone | Who's in a voice channel and for how long (yours by default). |
| `!mafk` | — | `[channel] [minutes]` | Manage Server | Set (or clear) the server's AFK voice channel and idle timeout (1, 5, 15, 30 or 60 minutes). |
| `!mjoin` | — | — | Everyone | Join your voice channel and stay (24/7). |
| `!mleave` | — | — | Everyone | Leave voice and pause the 24/7 rejoin. |
| `!mtts` | — | — | Everyone | Create/select the `#nova-tts` channel NOVA reads aloud (Hindi + English). |
| `!mttsdisable` | — | — | Everyone | Turn NOVA TTS off. |

### Configuration & diagnostics

- **Status:** Implemented
- **Provided by:** `common/diag.py`, `common/config.py`
- **What it does:** Discord-native settings panel (every feature module can be switched on/off independently without deleting data), diagnostics, personal timezone/preferences, configuration export/import and backup history. No shell/eval commands exist; tokens are never shown.
- **Required Discord permissions:** Manage Server for admin commands.
- **Configuration:** none.
- **Works independently?** Yes.

| Command | Aliases | Syntax | Access | What it does |
|---|---|---|---|---|
| `!mconfig` · `/mconfig` | `!msettings` | `[module]` | Manage Server | Open the settings panel (Manage Server). |
| `!mdiag` · `/mdiag` | `!mdiagnostics`, `!mbotinfo` | `[section]` | Manage Server | Diagnostics: `diag`, `diag errors`, `diag perms`, `diag config` (Manage Server). |
| `!mtimezone` · `/mtimezone` | `!mtz` | `[zone]` | Everyone | Set your timezone for reminders/events. |
| `!mprefs` · `/mprefs` | `!mpreferences` | — | Everyone | Your personal notification preferences and opt-outs. |
| `!mconfigexport` | — | — | Manage Server | Export this server's configuration as JSON (no tokens/secrets). |
| `!mconfigimport` | — | — | Manage Server | Import a configuration export (attach the .json); shows a preview first. |
| `!mconfigbackups` | — | `[restore_id]` | Manage Server | List configuration backups or restore one. |

## Feature comparison

| Area | ATLAS | NOVA |
|---|:-:|:-:|
| Moderation, automod, verification, tickets | ✅ | — |
| Suggestions, polls, events, reminders, engagement | ✅ | — |
| Statistics & leaderboards, games | ✅ | — |
| Logging, starboard, rules, embed builder | ✅ | — |
| Creator feeds (RSS/YouTube/Twitch) | ✅ (Twitch optional) | — |
| Self-service roles (basic) | ✅ | — |
| Role menus, reaction roles, temp roles, bulk tools, diagnostics | — | ✅ |
| Colour roles | — | ✅ |
| Temporary voice channels | — | ✅ |
| Channel & message tools | — | ✅ |
| 24/7 voice + TTS | ✅ (known issue) | ✅ (known issue) |
| Settings panel, diagnostics, export/import | ✅ `!config` | ✅ `!mconfig` |
| Local AI via LM Studio | ✅ optional | — |
