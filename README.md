# Job Watch

Checks Sri Lankan company careers pages and job boards every hour and sends you new Software Engineer, Full Stack, React and JavaScript jobs on Telegram and/or email. It runs free on GitHub Actions, so your computer doesn't need to be on.

**What it watches**
- 18 company job feeds (IFS, 99x, LSEG, Qoria, Orfium, Inivos, CUBE, Thomson Reuters…)
- About 50 company careers pages and job-board company pages (WSO2, Enactor, Cambio, Rooster/itpro.lk company pages…)
- itpro.lk's latest-jobs feed and software categories, plus Rooster.jobs, which catch companies without their own careers page

**How it works**
1. Every hour it reads each source in `sources.json` and the job boards in `config.yaml`.
2. It keeps jobs whose title matches your roles. Jobs from worldwide company feeds must also be located in Sri Lanka.
3. It compares against `state/seen.json` and sends only jobs you haven't been sent before, including the same job posted on two sites.
4. It saves the updated state back to the repo.

On the very first run it sends one summary of everything currently open, then only new jobs after that.

---

## Setup (about 15 minutes)

### 1. Put the files on GitHub
1. Sign in at [github.com](https://github.com) and click **New repository**. Name it `job-watch`. Private or public both work (see *Costs* below), then click **Create repository**.
2. On the new repo page, click **uploading an existing file** and drag in everything from this folder.
   - The `.github` folder is hidden on Mac and Windows and often gets skipped. Check that `.github/workflows/jobwatch.yml` appears in the repo after uploading. If it's missing, click **Add file → Create new file**, type `.github/workflows/jobwatch.yml` as the name, paste the file's contents, and commit.

### 2. Telegram alerts (free)
1. In Telegram, open **@BotFather**, send `/newbot`, and follow the prompts. Copy the **token** it gives you (looks like `123456:ABC-xyz…`).
2. Open your new bot and send it any message, e.g. "hi".
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`. Find `"chat":{"id":123456789` — that number is your **chat id**.

### 3. Email alerts through Gmail (free)
1. Turn on 2-Step Verification for your Google account if it isn't already.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an app password named "Job Watch", and copy the 16-letter password.

You can set up Telegram, email, or both. Any channel without secrets is skipped.

### 4. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add each of these:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat id number |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the 16-letter app password |
| `EMAIL_TO` | where to send alerts (can be the same Gmail; separate several with commas) |

Secrets are encrypted and never shown in logs.

### 5. Turn it on and test
1. Open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Job Watch → Run workflow**, choose **test-alert**, and run it. You should get a test message within a minute.
3. Run it again with **check-sources**. Open the run's log to see which sources work (`ok`), have no jobs (`EMPTY`), or fail (`FAIL`).
4. Run it once with **run**. You'll get the summary of current openings. From now on it runs by itself every hour.

If the "Save what was already sent" step fails with a permission error, go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and save.

---

## Changing what you get

Edit files directly on GitHub (open the file, click the pencil icon, then **Commit changes**). The next hourly run uses the new settings.

- **Job titles:** `config.yaml → titles.include / exclude`. Each line is a pattern matched against the job title. For example, add `'\bangular\b'` to include Angular roles, or `'\blead\b'` under exclude to skip lead roles.
- **Remote jobs (phase 2):** set `location.allow_remote_anywhere: true` to also get worldwide remote roles from the company feeds.
- **Add a company:** add an entry to `sources.json`:
  ```json
  {"company": "Example Ltd", "type": "html", "careers_url": "https://example.lk/careers", "enabled": true}
  ```
  Supported `type` values: `greenhouse`, `lever`, `ashby`, `workable`, `smartrecruiters`, `workday`, `recruitee`, `bamboohr` (these also need a `feed_url`), plus `html` for an ordinary careers page and `jobboard_only` with a `jobboard_url`.
- **Turn a source off:** set `"enabled": false` on it.
- **Daily health report:** sent at 9 AM Sri Lanka time. It lists broken sources and careers pages that link to a job platform the bot could read more reliably. Change or turn off with `alerts.health_report_hour`.
- **JavaScript-only careers pages** (HCLTech, Gallagher): set `fetching.use_browser: true` in `config.yaml` and `USE_BROWSER: 'true'` in the workflow file. This adds about a minute per run.

## Costs and limits
- **Public repo:** GitHub Actions is free and unlimited.
- **Private repo:** 2,000 free minutes a month. Each hourly run bills as 1 minute, so about 720–750 minutes a month. Turning on the browser option roughly doubles that.
- GitHub sometimes delays scheduled runs by 5–20 minutes when it's busy.
- If GitHub ever emails that it disabled the scheduled workflow, re-enable it from the Actions tab.
- LinkedIn, TopJobs and XpressJobs aren't scraped. LinkedIn forbids it and can restrict your account. Use their own job alerts with phone notifications alongside this.

## Running it on your own computer (optional)
```bash
pip install -r requirements.txt
python -m jobwatch --check-sources   # test every source
python -m jobwatch --dry-run         # see what would be sent
python -m pytest -q                  # offline tests
```
