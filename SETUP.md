# Setup

## 1. Install

```bash
git clone https://github.com/rubianaseem/webinar-follow-up-agent.git
cd webinar-follow-up-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.10 or newer. The rules use only the standard library; `anthropic` and `python-dotenv` are there for the optional Claude step.

## 2. Run the sample

```bash
python webinar_agent.py
python -m unittest discover tests
```

The sample covers the awkward rows real exports have: a double registration in different case, a rejoin split across two sessions, a replay viewer on a personal email, someone who watched under a different email, a competitor, the host, an opted-out contact who asked a question, an open-deal contact and a no-show at a customer account.

Everything lands in `out/`:

| File | What it is |
| --- | --- |
| `hubspot_webinar_import.csv` | One row per person to import, with webinar properties and the segment |
| `sdr_call_list.csv` | HOT_CALL people ranked by score, with reasons, call-by time and opener |
| `review_queue.csv` | Decisions for a human (different-email match, opted out but asked, customer account, hot without consent) |
| `quarantine.csv` | Test, internal, competitor and invalid rows, kept out of the CRM |
| `follow_up_plan.csv` | Every person, segment, action and owner |
| `follow_up_emails.md` | Email drafts for no-show, engaged and low-engagement segments |
| `webinar_brief.md` | A one-page summary for the marketing and sales leads |

## 3. Optional: add an Anthropic key

```bash
cp .env.example .env
# put your key after ANTHROPIC_API_KEY=
python webinar_agent.py
```

With a key, Claude rewrites the HOT_CALL openers and the brief. It only sees the plan (names, companies, titles, segments, scores, poll answers and questions), never changes a segment, and if the call fails the script keeps the template output. Use `--no-claude` to skip it even when a key is set.

## 4. Use your own data

You need three CSV files. Column names are matched loosely, so most exports work without renaming.

**Registrants.** Zoom: Reports, Webinar, Registration report. ON24: Reports, Registration. GoTo Webinar: Registrants export. You need email, first and last name, company, job title, registration time and, if you collect it, the consent checkbox and UTM source.

**Attendance.** Zoom: Attendee report (one row per join, which is why rejoins are summed). ON24: Attendee or engagement report including poll and Q&A columns. If your platform keeps Q&A in a separate file, paste the question into a `Questions Asked` column on the attendee row first.

**CRM snapshot.** In HubSpot, build a contacts view filtered to the registrant emails (or simply export all contacts at those domains) with Email, Contact owner, Lifecycle stage, an open deal stage column (a calculated property or a quick lookup from a deals export), a customer flag and marketing opt-out or unsubscribe status. In Salesforce, a contact report with Account Type and Opportunity Stage does the same job. If you skip this file the agent still runs, but it cannot protect opted-out or open-deal contacts, so do not skip it for real follow-up.

Then edit `webinar_policy.json`: webinar name, start time, length, your own domains, competitor domains, the poll phrases that mean "buying soon" and the score bands.

```bash
python webinar_agent.py --registrants reg.csv --attendance attendees.csv --crm contacts.csv --out out/oct-routing
```

Before importing, create the webinar properties in HubSpot (Settings, Properties) with the same labels as the CSV headers, test five rows, then import the rest with email as the unique key so existing contacts update rather than duplicate.

## 5. Extend it with an AI coding tool

Open the folder in Cursor, Claude Code, Codex CLI or Grok and ask for one change at a time, then rerun the tests. Prompts that work well:

- "Add a `--webinar-series` mode that reads several attendance files and scores people who attended two or more sessions higher."
- "Read questions from a separate Q&A export and join them on email."
- "Add a Slack message for each HOT_CALL row posted to the owning SDR, mocked unless SLACK_WEBHOOK_URL is set."
- "Add a test that a contact with an open deal is never put on the SDR call list."

## 6. Troubleshooting

- **Everyone is NO_SHOW_REPLAY.** The attendance email column did not match. Check the header contains "Email"; Zoom sometimes exports "User Email".
- **Minutes look too high.** Hosts and panelists often appear in the attendee report. Add your domain to `own_domains`.
- **No questions picked up.** The column needs "Question" or "Q&A" in its header.
- **`ModuleNotFoundError: anthropic`.** You are outside the venv. Run `source .venv/bin/activate` and try again, or ignore it: the rules run without it.
- **Claude step skipped.** The message in the terminal names the error. The outputs are still complete from the templates.
