# Webinar Follow-up Agent

An agent that turns your webinar platform's registration and attendance exports into a follow-up plan: one clean record per person, an engagement score with reasons, a segment and next action, an SDR call list with openers built from what each person asked, and CRM-ready webinar properties.

Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

## The pain point

After most B2B webinars, everyone gets the same "thanks for coming, here's the recording" email. The person who asked about pricing in the Q&A gets the same message as the person who left after three minutes, and the open-deal contact gets dropped into nurture next to the competitor who signed up to watch. The data to tell them apart is in the platform exports. It just never makes it into the CRM in a usable shape.

> "We usually do the standard "Thanks for coming + Here is the link to the recording" email, but the conversion to actual meetings is pretty low recently."
> [r/b2bmarketing: "Post-Webinar follow-ups: The "Here is the recording" email feels dead"](https://www.reddit.com/r/b2bmarketing/comments/1qxqu2l/postwebinar_followups_the_here_is_the_recording/)

> "What's the best way to segment webinar leads in HubSpot (registered, no-show, attended, stayed X mins) for better follow-up when the data comes from a webinar platform integration?"
> [r/hubspot: "Best way to segment webinar leads in HubSpot for follow-up"](https://www.reddit.com/r/hubspot/comments/1qisg6k/best_way_to_segment_webinar_leads_in_hubspot_for/)

## What it does

- Merges messy exports: re-registrations with different casing, rejoins summed and capped at the webinar length, and people who watched under a different email matched on name and company (and held for a human to confirm).
- Quarantines test entries, your own staff and competitors so they never reach the CRM or a sequence.
- Checks a CRM snapshot: opted-out contacts are logged only, open-deal and customer contacts go to their owner as a heads-up, and new faces at customer accounts are flagged.
- Scores engagement (live or replay, percent watched, buying-window poll answer, questions with intent words, seniority) and shows every reason behind the score.
- Puts each person in one segment with one action: HOT_CALL, AE_HEADS_UP, ENGAGED_NURTURE, LOW_ENGAGEMENT, NO_SHOW_REPLAY, SUPPRESS_MARKETING or QUARANTINE.
- Writes a HubSpot import, an SDR call list with a call-by time, a review queue, segment email drafts and a brief. Rules run with no API key. With an Anthropic key, Claude writes the openers and brief from the plan only.

## Where each step runs

1. **GitHub** holds the code. You copy it to your laptop with `git clone`.
2. **Your webinar platform and CRM** are where you export three files: registrants, attendance and a contact snapshot. [SETUP.md](SETUP.md) shows the Zoom, ON24 and HubSpot exports.
3. **Terminal** (Mac Terminal or the terminal inside Cursor) runs the script. It reads the CSVs and writes everything to `out/`.
4. **Claude is optional.** With no key it uses built-in rules and templates. With a key in `.env` it writes sharper call openers and the brief. It still runs in the terminal.
5. **Back in your tools**, you clear the review queue, hand the call list to SDRs, then import `out/hubspot_webinar_import.csv` and build lists from the segment property. The agent never writes to your CRM or sends an email itself.

## Quick start

```bash
git clone https://github.com/rubianaseem/webinar-follow-up-agent.git
cd webinar-follow-up-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python webinar_agent.py
python -m unittest discover tests
```

On your own data: `python webinar_agent.py --registrants reg.csv --attendance attendees.csv --crm contacts.csv --policy webinar_policy.json`

## Example output

```
People after merge: 8. Mode: rules only (no API key)
  100  HOT_CALL           Priya Raman            Northwind Logistics  MERGED_2_REGISTRATIONS
   96  SUPPRESS_MARKETING Dana White             Medcore Health
   74  AE_HEADS_UP        James Okafor           Brightwave
   57  ENGAGED_NURTURE    Marco Bianchi          Ledgerly
   11  LOW_ENGAGEMENT     Sam Lee                freelance            PERSONAL_EMAIL
   10  NO_SHOW_REPLAY     Elena Costa            Quillstack           CUSTOMER_ACCOUNT
   69  QUARANTINE         Alex Grant             RouteIQ              COMPETITOR
   60  QUARANTINE         Webinar Host           YourCo               NOT_REGISTERED, MINUTES_CAPPED, INTERNAL
SDR calls due: 1. Review queue: 3. Rows ready to import: 5.
```

Dana is the case a score-only setup gets wrong: she watched 41 minutes and asked for the template, so she scores 96, but she opted out of marketing. She is logged, not sequenced, and her owner decides whether to answer her question one to one. Priya registered twice and rejoined once; she becomes one record with 41 minutes and a call due within 24 hours that opens with her own question about Salesforce round robin.

## More

- [SETUP.md](SETUP.md): install, API key, exporting the three files from Zoom, ON24, GoTo or HubSpot, extending with Cursor, Claude Code, Codex CLI or Grok, and troubleshooting.
- [docs/DETAILS.md](docs/DETAILS.md): cost note, stack-swap table, customising the policy file, and how this runs today versus what production would add.
- [docs/FOLLOW_UP_PATTERN.md](docs/FOLLOW_UP_PATTERN.md): the transferable GTM Engineering pattern behind it and why each rule is ordered the way it is.
