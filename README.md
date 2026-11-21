# Strava Agent

Chat with an AI coach about your own Strava training. Ask questions like
*"How did my running go this month?"* or *"Show me a bar chart of my weekly
distance"* and get answers and charts built from your real Strava activities.

It runs on your own computer, in your web browser. This guide walks you
through setting it up on a **Mac** or a **Windows** PC. No programming
experience is needed: you'll copy and paste a few commands.

---

## What you'll need

- A **Mac** or a **Windows 10/11** PC, connected to the internet.
- A **Strava** account with some activities.
- A **paid Claude plan** (Pro, Max, Team or Enterprise). The free Claude plan
  won't work. The app uses your Claude plan to think, so questions count
  towards your Claude usage.
- About **30 minutes** for the first-time setup.

## How it works (in one minute)

There are three pieces:

1. **The Strava connector** fetches your activities from Strava.
2. **Claude** (the AI) reads that data and answers your questions.
3. **The chat app** is the web page where you type questions and see answers.

Pieces 1 and 3 run on your computer, each in its own **terminal** window (a
window where you type commands). You start both whenever you want to use the
app.

---

## One-time setup

Do these steps once, in order. When a step shows a command, copy it, paste it
into the terminal and press **Enter**. Where a step differs between Mac and
Windows, use the part for your computer.

### Step 1: Open a terminal

**Mac:** press **⌘ Command + Space**, type **Terminal** and press **Enter**.

**Windows:** click the **Start** button, type **PowerShell** and open
**Windows PowerShell**. (Use PowerShell, not "Command Prompt"; the commands in
this guide are written for PowerShell.)

> To paste into the terminal: **⌘ Command + V** on a Mac. On Windows,
> **Ctrl + V** or a right-click.

### Step 2: Install uv (runs the app for you)

uv downloads the right version of Python and everything the app needs.

**Mac:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Close the terminal window and open a new one**, so it notices the new
program. Then check it worked. This should print a version number:

```
uv --version
```

### Step 3: Install Claude Code and log in

The app talks to Claude through the Claude Code program.

**Mac:**

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Windows:**

```powershell
irm https://claude.ai/install.ps1 | iex
```

**Close the terminal and open a new one** again, then log in. Your browser
will open; sign in with your Claude account and approve:

```
claude auth login --claudeai
```

Check it worked. This should print a version number:

```
claude --version
```

### Step 4: Download this project

1. Go to <https://github.com/mhobby/StravaAgent>.
2. Click the green **Code** button, then **Download ZIP**.
3. Unzip it into your **Documents** folder. You should end up with a folder
   called **StravaAgent-main** inside Documents. (On a Mac, double-click the
   ZIP. On Windows, right-click it, choose **Extract All…**, and pick
   Documents.)

Now point the terminal at that folder. **You'll do this every time you open a
new terminal for this app.**

**Mac:**

```bash
cd ~/Documents/StravaAgent-main
```

**Windows:**

```powershell
cd $HOME\Documents\StravaAgent-main
```

> If you put the folder somewhere else on a Mac: type `cd ` (with a space),
> drag the folder from Finder onto the terminal window and press Enter.

### Step 5: Create your own Strava "app"

Strava requires every program that reads your data to be registered. This
takes two minutes and only gives access to *your* account.

1. Log in to Strava in your browser and go to
   <https://www.strava.com/settings/api>.
2. Fill in the form:

   | Field | What to enter |
   | --- | --- |
   | Application Name | Anything that doesn't contain the word "Strava", e.g. **My Training Coach** |
   | Category | Any, e.g. **Training** |
   | Club | Leave empty |
   | Website | `http://localhost` |
   | Application Description | Optional |
   | **Authorization Callback Domain** | `127.0.0.1` (exactly this) |

3. Tick the agreement box and click **Create**.
4. If Strava asks for an icon, upload **icon.png** from the StravaAgent-main
   folder.
5. You'll now see your app's settings. Keep this page open: you need the
   **Client ID** (a number) and the **Client Secret** (click **Show** to
   reveal it) in the next step.

### Step 6: Save your Strava keys

These commands create two small files in the project folder holding your
keys. Replace `PASTE_CLIENT_ID` and `PASTE_CLIENT_SECRET` with the values
from Strava, **keeping the quote marks** around them.

**Mac:**

```bash
printf '%s' 'PASTE_CLIENT_ID' > CLIENT_ID
printf '%s' 'PASTE_CLIENT_SECRET' > CLIENT_SECRET
```

**Windows:**

```powershell
Set-Content -Path CLIENT_ID -Value 'PASTE_CLIENT_ID' -NoNewline
Set-Content -Path CLIENT_SECRET -Value 'PASTE_CLIENT_SECRET' -NoNewline
```

> Treat the Client Secret like a password: don't share it or post it online.

### Step 7: Install the app's components

This downloads Python and the libraries the app uses. It can take a few
minutes the first time.

```
uv sync
```

### Step 8: Connect the app to your Strava account

```
uv run python auth.py
```

Your browser opens a Strava page asking you to authorise your app. Click
**Authorize**. The browser then shows **"Strava authorization complete"**, and
the terminal prints **"Saved Strava tokens"**. You only need to do this once;
the app keeps itself logged in after that.

**Setup is done.** 🎉

---

## Using the app (every time)

You need **two terminal windows**: one for the Strava connector and one for
the chat app. Leave both open while you use the app.

### Window 1: start the Strava connector

Open a terminal, go to the project folder (see Step 4), and run:

```
uv run python strava_mcp_server.py
```

Wait until you see a line ending in **`Uvicorn running on
http://127.0.0.1:8000`**. Leave this window open.

### Window 2: start the chat app

Open a **second** terminal window (on a Mac, press **⌘ Command + N** in
Terminal; on Windows, open PowerShell again from the Start menu). Go to the
project folder again (see Step 4), and run:

```
uv run streamlit run app.py
```

> The very first time, it may ask for your email address. Just press
> **Enter** to skip.

Your browser opens the app at <http://localhost:8501>. If it doesn't, open
that address yourself.

### Asking questions

Type into the box at the bottom of the page, for example:

- *How much did I run in the last 2 weeks?*
- *Bar chart of my weekly running distance for the last 8 weeks*
- *Line chart of average heart rate across my last 10 runs*
- *Compare my moving time per sport this year as a bar chart*
- *Which of those was my hardest week?* (it remembers the conversation)

Tips:

- **Charts:** ask for a "line chart" (trends over time) or a "bar chart"
  (comparing amounts). Under each chart, **Data** shows the numbers as a table.
- **Strava calls** under an answer shows what the app fetched from Strava.
- **New conversation** (left sidebar) starts fresh.
- Answers usually take **10 to 60 seconds**; the app fetches your data and
  thinks before replying.

### Stopping the app

In each terminal window, press **Ctrl + C** (on both Mac and Windows), or
simply close the windows.

---

## Your privacy

- The app runs on your computer, and only your computer can open it.
- To answer questions, the Strava data it fetches is sent to Claude
  (Anthropic), the same as when you chat with Claude yourself.
- Your keys and login are stored in the project folder in **CLIENT_ID**,
  **CLIENT_SECRET** and **.strava_tokens.json** (a hidden file). Don't share
  the folder with anyone.
- To disconnect the app from Strava at any time, go to
  <https://www.strava.com/settings/apps> and revoke its access.

---

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `command not found` (Mac) or `is not recognized` (Windows) for `uv` or `claude` | Close the terminal and open a new one. If it still fails, run that program's install command (Step 2 or 3) again. |
| Windows: `running scripts is disabled on this system` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer **Y**, then try again. |
| `No such file or directory` / `Cannot find path`, or `uv` says it can't find the project | The terminal isn't in the project folder. Run the `cd` command from Step 4. |
| `Missing Strava credential` | The key files are missing or empty. Redo Step 6 in the project folder. |
| Strava's page says **Bad Request** or `redirect_uri invalid` when authorising | On <https://www.strava.com/settings/api>, set **Authorization Callback Domain** to exactly `127.0.0.1`, then redo Step 8. |
| `No Strava authorization within 300s` | You didn't click **Authorize** in time. Run Step 8 again. |
| The app shows **Strava MCP server not reachable** | Window 1 isn't running. Start the Strava connector (see "Using the app"). |
| `address already in use` or `error while attempting to bind` | That part is already running in another terminal window. Use that one, or close it and start again. |
| The app shows **Strava rate limit hit** | Strava allows 100 requests per 15 minutes. Wait 15 minutes and ask again. |
| Strava errors after a long break, or you want to log in as someone else | Delete the saved login (Mac: `rm .strava_tokens.json`, Windows: `Remove-Item .strava_tokens.json`) in the project folder, then redo Step 8. |
| Errors mentioning Claude, login or usage limits | Run `claude auth login --claudeai` again. Check your Claude plan is a paid one and you haven't hit its usage limit. |

### Updating to a newer version

Download the new ZIP (Step 4), then copy **CLIENT_ID**, **CLIENT_SECRET** and
**.strava_tokens.json** from your old folder into the new one. (The last one
is hidden: on a Mac press **⌘ Command + Shift + .** in Finder to show hidden
files. On Windows, in File Explorer choose **View → Show → Hidden items**.) Then
run `uv sync` once in the new folder.

---

## For developers

| File | Purpose |
| --- | --- |
| `strava_mcp_server.py` | Local MCP server (FastMCP, streamable HTTP on `127.0.0.1:8000/mcp`) wrapping the Strava REST API v3. Trims responses and converts units. |
| `auth.py` | Strava OAuth (authorization code flow) with token persistence and auto-refresh. |
| `strava_agent.py` | `StravaAgent`: LangGraph tool loop with Claude (`ChatClaudeCli`) and the MCP tools, with per-thread memory. |
| `plot_tools.py` | `plot_line_chart` / `plot_bar_chart` LangChain tools that return chart specs as ToolMessage artifacts, plus Altair rendering. |
| `app.py` | Streamlit chat frontend. |
| `scratch.ipynb` | Notebook for experimenting. |

- Inspect the MCP tools interactively: `uv run mcp dev strava_mcp_server.py`.
- Strava's official MCP server (`mcp.strava.com`) only accepts approved AI
  assistants, which is why this project runs its own.
- `langchain-claude-cli` 1.2.1 sometimes appends a second "I've already
  answered" reply after tool calls. `StravaAgent._drop_continuation_reply`
  strips it from the displayed text; see its docstring before changing how
  messages are stored.
