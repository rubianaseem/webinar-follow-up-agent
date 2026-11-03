# Details

## Cost note

With no key, it costs nothing to run. With a key, there is one Claude call per run, sending a compact plan rather than the raw exports, so the cost depends on the number of people in the plan and your model's current pricing. Check the Anthropic pricing page for the model you set in `ANTHROPIC_MODEL`.

## Stack swap

| Piece | Here | Swap for |
| --- | --- | --- |
| Webinar platform | CSV exports | Zoom, ON24, GoTo Webinar, Livestorm, Goldcast, HubSpot Events |
| CRM snapshot | CSV | HubSpot contacts export, Salesforce report, a warehouse table |
| Rules | `webinar_policy.json` | The same JSON in n8n, Make or a HubSpot custom code action |
| Writing | Templates, optional Claude | Any LLM behind the same `maybe_claude` function |
| Output | CSV and markdown | HubSpot import, Salesforce Data Loader, a Slack message per SDR |

## Customise for your stack

- **Score weights and bands** live in `webinar_policy.json`. Change them after looking at which segments booked meetings over a few webinars, not before.
- **Buying poll phrases** should match the exact poll options you run. One well-designed poll ("When are you looking to fix this?") is worth more than any other signal here.
- **Intent words** are matched as substrings, so "integrat" catches integrate and integration.
- **The SLA** is `call_within_hours` after the webinar ends.
- **Segments** are decided in `segment()`. The order is deliberate; see [FOLLOW_UP_PATTERN.md](FOLLOW_UP_PATTERN.md).

## How this runs today (and what production would add)

Today it is a script you run once after a webinar, on exports you download by hand. It never writes to the CRM, never sends email and never calls anyone.

Production would add:

- Pulling registrants and attendance from the platform API on a schedule after the session ends, rather than CSV downloads.
- Reading the CRM live for owner, opt-out, open deals and customer status, including account-level lookups rather than domain matching.
- Writing the webinar properties through the CRM API with a dry-run mode and a change log, still keeping the review queue manual.
- Posting each HOT_CALL row to the owning SDR in Slack with the call-by time, and a reminder if it is not logged by then.
- Consent rules per country, checked against your legal team's guidance, instead of a single consent flag.
- A feedback loop: which segments booked meetings, so the weights are tuned on your own results.
