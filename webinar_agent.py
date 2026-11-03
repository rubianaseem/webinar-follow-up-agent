#!/usr/bin/env python3
"""Webinar Follow-up Agent.

Turns a webinar platform's registration and attendance exports into a
follow-up plan: one merged record per person, an engagement score, a
segment with a next action, an SDR call list with openers built from what
each person actually asked, CRM-ready webinar properties, a review queue
and a short brief for the marketing and sales leads.

Runs on rules with no API key. With ANTHROPIC_API_KEY set, Claude rewrites
the call openers and the brief from the plan only (it never sees raw rows
it was not given and never changes a segment).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")

# Segments, in priority order. The first rule that matches wins.
QUARANTINE = "QUARANTINE"            # test, internal, competitor, invalid email
SUPPRESS = "SUPPRESS_MARKETING"      # opted out of marketing: log attendance only
AE_HEADS_UP = "AE_HEADS_UP"          # open deal or customer: tell the owner, no SDR touch
HOT_CALL = "HOT_CALL"                # SDR calls within the SLA, opener from their question
ENGAGED = "ENGAGED_NURTURE"          # watched most of it: next-step content, not a call
LOW = "LOW_ENGAGEMENT"               # dropped early or skimmed the replay
NO_SHOW = "NO_SHOW_REPLAY"           # registered, never watched


# ---------------------------------------------------------------- helpers
def norm_email(value: str) -> str:
    return (value or "").strip().lower()


def domain_of(email: str) -> str:
    return email.split("@", 1)[1] if "@" in email else ""


def yes(value: str) -> bool:
    return (value or "").strip().lower() in {"yes", "y", "true", "1", "opted out", "checked"}


def clean_name(value: str) -> str:
    return " ".join(w.capitalize() for w in (value or "").strip().split())


def norm_company(value: str) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"[.,]", "", v)
    v = re.sub(r"\b(ltd|limited|inc|llc|gmbh|plc|corp|co)\b", "", v)
    return " ".join(v.split())


def parse_time(value: str):
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def pick(row: dict, *names: str) -> str:
    """Read the first matching column, case-insensitive, so exports from
    Zoom, ON24, GoTo or HubSpot can be used without renaming headers."""
    lower = {k.lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()]:
            return lower[name.lower()]
    for key, val in lower.items():  # partial match, e.g. "Poll: When are you..."
        for name in names:
            if name.lower() in key and val:
                return val
    return ""


# ---------------------------------------------------------------- model
@dataclass
class Person:
    email: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    job_title: str = ""
    country: str = ""
    registered: bool = False
    registered_at: str = ""
    utm_source: str = ""
    consent: bool = False
    attended: bool = False
    session_type: str = ""
    minutes: int = 0
    pct_watched: int = 0
    sessions: int = 0
    poll_answer: str = ""
    questions: list = field(default_factory=list)
    duplicate_registrations: int = 0
    matched_by: str = "email"
    alt_email: str = ""
    crm_owner: str = ""
    crm_lifecycle: str = ""
    crm_open_deal: str = ""
    crm_customer: bool = False
    crm_opt_out: bool = False
    account_owner: str = ""
    score: int = 0
    score_reasons: list = field(default_factory=list)
    segment: str = ""
    action: str = ""
    flags: list = field(default_factory=list)
    call_by: str = ""
    opener: str = ""

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email


# ---------------------------------------------------------------- steps
def merge_registrants(rows: list[dict]) -> dict[str, Person]:
    """Step 1a: one Person per email. Re-registrations (different case,
    trailing spaces, a second form fill) are merged, keeping the earliest
    registration and the most complete field values."""
    people: dict[str, Person] = {}
    for row in rows:
        email = norm_email(pick(row, "Email", "Email Address", "Registrant Email"))
        if not email:
            continue
        p = people.get(email)
        if p is None:
            p = Person(email=email)
            people[email] = p
        else:
            p.duplicate_registrations += 1
        p.registered = True
        p.first_name = p.first_name or clean_name(pick(row, "First Name", "First"))
        p.last_name = p.last_name or clean_name(pick(row, "Last Name", "Last"))
        p.company = p.company or pick(row, "Company", "Organization")
        p.job_title = p.job_title or pick(row, "Job Title", "Title")
        p.country = p.country or pick(row, "Country")
        p.utm_source = p.utm_source or pick(row, "UTM Source", "Source").lower()
        when = pick(row, "Registered At", "Registration Time")
        if when and (not p.registered_at or when < p.registered_at):
            p.registered_at = when
        p.consent = p.consent or yes(pick(row, "Marketing Consent", "Consent", "Opt In"))
    return people


def merge_attendance(people: dict[str, Person], rows: list[dict], policy: dict, review: list) -> None:
    """Step 1b: attach attendance. Rejoins are summed and capped at the
    webinar length. An attendee whose email is not a registrant is matched
    on name plus company, and that match is held for a human to confirm."""
    length = int(policy["webinar_minutes"])
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(norm_email(pick(row, "Attendee Email", "Email")), []).append(row)

    for email, sessions in by_key.items():
        if not email:
            continue
        first = sessions[0]
        target = people.get(email)
        if target is None:
            full = pick(first, "Name", "Attendee Name").strip().lower()
            comp = norm_company(pick(first, "Company", "Organization"))
            for p in people.values():
                if p.name.lower() == full and comp and norm_company(p.company) == comp:
                    target = p
                    target.matched_by = "name+company"
                    target.alt_email = email
                    break
        if target is None:  # walked in without registering
            name = pick(first, "Name", "Attendee Name").split()
            target = Person(email=email, first_name=clean_name(name[0]) if name else "",
                            last_name=clean_name(" ".join(name[1:])) if len(name) > 1 else "",
                            company=pick(first, "Company", "Organization"))
            target.flags.append("NOT_REGISTERED")
            people[email] = target

        total = 0
        types = set()
        for s in sessions:
            mins = pick(s, "Minutes Attended", "Duration", "Time in Session")
            if mins.isdigit():
                total += int(mins)
            else:
                start, end = parse_time(pick(s, "Join Time")), parse_time(pick(s, "Leave Time"))
                if start and end:
                    total += max(0, int((end - start).total_seconds() // 60))
            types.add("replay" if "demand" in pick(s, "Session Type").lower() or "replay" in pick(s, "Session Type").lower() else "live")
            poll = pick(s, "Poll")
            if poll and not target.poll_answer:
                target.poll_answer = poll
            q = pick(s, "Questions Asked", "Question", "Q&A")
            if q:
                target.questions.append(q)
        if total > length:
            target.flags.append("MINUTES_CAPPED")
        target.attended = True
        target.sessions += len(sessions)
        target.minutes = max(target.minutes, min(total, length))
        target.pct_watched = round(100 * target.minutes / length)
        target.session_type = "live" if "live" in types else "replay"
        if target.matched_by == "name+company":
            review.append({"email": target.email, "name": target.name, "reason": "ATTENDED_UNDER_DIFFERENT_EMAIL",
                           "detail": f"Watched as {target.alt_email}; matched on name and company. Confirm it is the same person before merging in the CRM."})


def quarantine_reason(p: Person, policy: dict) -> str:
    dom = domain_of(p.email)
    if not EMAIL_RE.match(p.email):
        return "INVALID_EMAIL"
    if any(t in p.email for t in policy["test_patterns"]):
        return "TEST_ENTRY"
    if dom in policy["own_domains"]:
        return "INTERNAL"
    if dom in policy["competitor_domains"]:
        return "COMPETITOR"
    return ""


def match_crm(people: dict[str, Person], crm_rows: list[dict]) -> None:
    """Step 2: look each person up in a CRM snapshot by email, then by
    domain so a new face at a customer or open-deal account is spotted."""
    by_email = {norm_email(pick(r, "Email")): r for r in crm_rows}
    customer_domains: dict[str, str] = {}
    for r in crm_rows:
        if yes(pick(r, "Is Customer")) or pick(r, "Lifecycle Stage").lower() == "customer":
            customer_domains[domain_of(norm_email(pick(r, "Email")))] = pick(r, "Contact Owner")
    for p in people.values():
        r = by_email.get(p.email)
        if r:
            p.crm_owner = pick(r, "Contact Owner", "Owner")
            p.crm_lifecycle = pick(r, "Lifecycle Stage").lower()
            p.crm_open_deal = pick(r, "Open Deal Stage")
            p.crm_customer = yes(pick(r, "Is Customer")) or p.crm_lifecycle == "customer"
            p.crm_opt_out = yes(pick(r, "Marketing Opt Out", "Unsubscribed"))
        dom = domain_of(p.email)
        if dom in customer_domains and not p.crm_customer:
            p.account_owner = customer_domains[dom]
            p.flags.append("CUSTOMER_ACCOUNT")


def score(p: Person, policy: dict) -> None:
    """Step 3: engagement plus a little fit. Every point has a reason so a
    rep can see why someone is on the call list."""
    w, s, why = policy["weights"], 0, []
    if p.attended:
        if p.session_type == "live":
            s += w["attended_live"]; why.append("live")
        else:
            s += w["watched_replay"]; why.append("replay")
        pts = round(w["pct_watched_max"] * p.pct_watched / 100)
        s += pts; why.append(f"{p.pct_watched}% watched")
    if p.poll_answer and any(k in p.poll_answer.lower() for k in policy["buying_poll_phrases"]):
        s += w["buying_poll"]; why.append("buying-window poll")
    if p.questions:
        s += w["asked_question"]; why.append("asked a question")
        text = " ".join(p.questions).lower()
        if any(k in text for k in policy["intent_keywords"]):
            s += w["intent_question"]; why.append("intent words in question")
    if any(re.search(rf"\b{re.escape(t)}\b", p.job_title.lower()) for t in policy["senior_title_words"]):
        s += w["senior_title"]; why.append("senior title")
    if domain_of(p.email) in policy["personal_domains"]:
        s += w["personal_email"]; why.append("personal email")
        p.flags.append("PERSONAL_EMAIL")
    p.score = max(0, min(100, s))
    p.score_reasons = why


def segment(p: Person, policy: dict, review: list, call_deadline: datetime) -> None:
    """Step 4: one segment and one next action per person. Order matters:
    safety first (quarantine, opt-out), then existing relationships, then
    engagement."""
    q = quarantine_reason(p, policy)
    bands = policy["bands"]
    if q:
        p.segment, p.action = QUARANTINE, f"Do not import or follow up ({q})."
        p.flags.append(q)
        return
    if p.crm_opt_out:
        p.segment = SUPPRESS
        p.action = "Log attendance on the record only. No marketing email or sequence."
        if p.questions:
            review.append({"email": p.email, "name": p.name, "reason": "OPTED_OUT_BUT_ASKED",
                           "detail": f"Opted out of marketing but asked: \"{p.questions[0]}\". Owner ({p.crm_owner or 'none'}) decides on a one-to-one answer."})
        return
    if p.crm_open_deal or p.crm_customer:
        p.segment = AE_HEADS_UP
        what = f"open deal in {p.crm_open_deal}" if p.crm_open_deal else "existing customer"
        p.action = f"Tell {p.crm_owner or 'the owner'} ({what}) what they watched. Keep them out of SDR outreach and webinar nurture."
        return
    if not p.attended:
        p.segment, p.action = NO_SHOW, "Send the replay with the most-asked question as the hook."
    elif p.score >= bands["hot"]:
        p.segment = HOT_CALL
        p.call_by = call_deadline.strftime("%Y-%m-%d %H:%M")
        p.action = f"SDR call by {p.call_by}, opening with their question."
    elif p.score >= bands["engaged"]:
        p.segment, p.action = ENGAGED, "Send the next-step resource that matches their poll answer."
    else:
        p.segment, p.action = LOW, "Send the replay and a two-minute clip; no call."
    if "CUSTOMER_ACCOUNT" in p.flags:
        review.append({"email": p.email, "name": p.name, "reason": "CONTACT_AT_CUSTOMER_ACCOUNT",
                       "detail": f"Works at a customer account owned by {p.account_owner}. Owner decides between expansion outreach and the {p.segment} play."})
        p.action += f" Hold until {p.account_owner} confirms."
    if p.segment == HOT_CALL and not p.consent:
        p.flags.append("NO_CONSENT_RECORDED")
        review.append({"email": p.email, "name": p.name, "reason": "HOT_WITHOUT_CONSENT",
                       "detail": "Scored hot but registration shows no marketing consent. Check the lawful basis for a call in their country."})
    if p.duplicate_registrations:
        p.flags.append(f"MERGED_{p.duplicate_registrations + 1}_REGISTRATIONS")


def template_opener(p: Person, policy: dict) -> str:
    topic = policy["webinar_name"]
    if p.questions:
        q = p.questions[0].rstrip("?")
        return (f"Hi {p.first_name}, it's [rep] from [company]. You asked on the {topic} session: \"{q}?\" "
                f"I didn't want to leave that with a generic answer. Have you got two minutes now, or shall I book 15 this week?")
    return (f"Hi {p.first_name}, it's [rep] from [company]. You stayed for most of {topic}"
            f"{' and said you are ' + p.poll_answer.lower() if p.poll_answer else ''}. What made you join?")


SEGMENT_EMAILS = {
    NO_SHOW: ("Sorry we missed you: {webinar} replay",
              "Hi {first},\n\nYou registered for {webinar} but couldn't make it. Here is the replay: [link].\n\n"
              "The question most people asked was \"{top_q}\". The answer starts at [timestamp].\n\n[Sender]"),
    ENGAGED: ("The {webinar} template, as promised",
              "Hi {first},\n\nThanks for staying for most of {webinar}. Here is the template we walked through: [link].\n\n"
              "If you want it mapped to your own CRM, reply and I'll set up 20 minutes.\n\n[Sender]"),
    LOW: ("{webinar} in two minutes",
          "Hi {first},\n\nHere is a two-minute clip of the part people found most useful, plus the full replay: [link].\n\n[Sender]"),
}


def maybe_claude(plan: dict, hot: list[Person], brief_text: str) -> tuple[dict, str, bool]:
    """Optional step: Claude rewrites openers and the brief from the plan.
    Any failure falls back to the template output already built."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {}, brief_text, False
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "You help a marketing ops team follow up after a B2B webinar. Using ONLY the JSON plan below, "
            "return JSON with two keys: 'openers' (object of email -> a 2-sentence phone opener for each HOT_CALL "
            "person that answers the spirit of their question, plain British English, no hype, keep [rep] and [company] placeholders) "
            "and 'brief' (a markdown brief under 250 words for the marketing and sales leads). Do not invent numbers, "
            "names or facts that are not in the plan. Do not change any segment.\n\n" + json.dumps(plan, indent=1)
        )
        msg = client.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"), max_tokens=1500,
                                     messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        openers = {k: v for k, v in data.get("openers", {}).items() if k in {p.email for p in hot}}
        return openers, data.get("brief") or brief_text, True
    except Exception as exc:  # noqa: BLE001
        print(f"Claude step skipped ({type(exc).__name__}); using built-in templates.", file=sys.stderr)
        return {}, brief_text, False


# ---------------------------------------------------------------- outputs
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def build_brief(people: list[Person], review: list, policy: dict) -> str:
    counts: dict[str, int] = {}
    for p in people:
        counts[p.segment] = counts.get(p.segment, 0) + 1
    real = [p for p in people if p.segment != QUARANTINE]
    attended = [p for p in real if p.attended]
    lines = [f"# Webinar follow-up brief: {policy['webinar_name']}", "",
             f"{len(real)} people after merging and quarantine; {len(attended)} watched (live or replay), "
             f"{sum(1 for p in real if p.registered and not p.attended)} registered and did not watch.", "",
             "## Segments", ""]
    for seg in (HOT_CALL, AE_HEADS_UP, ENGAGED, LOW, NO_SHOW, SUPPRESS, QUARANTINE):
        if counts.get(seg):
            lines.append(f"- {seg}: {counts[seg]}")
    lines += ["", "## Call today", ""]
    for p in sorted((p for p in people if p.segment == HOT_CALL), key=lambda x: -x.score):
        lines.append(f"- {p.name}, {p.job_title} at {p.company} (score {p.score}): {p.questions[0] if p.questions else p.poll_answer}")
    if not any(p.segment == HOT_CALL for p in people):
        lines.append("- Nobody crossed the hot threshold.")
    lines += ["", "## Owner heads-up", ""]
    for p in (p for p in people if p.segment == AE_HEADS_UP):
        lines.append(f"- {p.crm_owner or 'Unowned'}: {p.name} ({p.company}) watched {p.pct_watched}% live; {p.crm_open_deal or 'customer'}.")
    lines += ["", "## Needs a human", ""]
    lines += [f"- {r['name']}: {r['reason']}. {r['detail']}" for r in review] or ["- Nothing."]
    qs = [q for p in real for q in p.questions]
    if qs:
        lines += ["", "## Questions asked (use them in the replay email and content plan)", ""] + [f"- {q}" for q in qs]
    return "\n".join(lines) + "\n"


def run(reg_path: Path, att_path: Path, crm_path: Path | None, policy_path: Path, out_dir: Path,
        use_claude: bool = True) -> dict:
    policy = json.loads(policy_path.read_text())
    review: list[dict] = []
    people = merge_registrants(read_csv(reg_path))
    merge_attendance(people, read_csv(att_path), policy, review)
    match_crm(people, read_csv(crm_path) if crm_path and crm_path.exists() else [])
    start = parse_time(policy["webinar_start"]) or datetime.now()
    deadline = start + timedelta(minutes=int(policy["webinar_minutes"])) + timedelta(hours=int(policy["call_within_hours"]))
    for p in people.values():
        score(p, policy)
        segment(p, policy, review, deadline)
        if p.segment == HOT_CALL:
            p.opener = template_opener(p, policy)
    ordered = sorted(people.values(), key=lambda p: (p.segment == QUARANTINE, -p.score, p.email))

    brief = build_brief(ordered, review, policy)
    hot = [p for p in ordered if p.segment == HOT_CALL]
    plan = {"webinar": policy["webinar_name"],
            "people": [{k: v for k, v in asdict(p).items() if k in
                        ("email", "first_name", "company", "job_title", "segment", "score", "poll_answer", "questions", "pct_watched")}
                       for p in ordered if p.segment != QUARANTINE],
            "review": review}
    used_claude = False
    if use_claude:
        openers, brief, used_claude = maybe_claude(plan, hot, brief)
        for p in hot:
            p.opener = openers.get(p.email, p.opener)

    out_dir.mkdir(parents=True, exist_ok=True)
    importable = [p for p in ordered if p.segment != QUARANTINE and not any(
        r["email"] == p.email and r["reason"] == "ATTENDED_UNDER_DIFFERENT_EMAIL" for r in review)]
    write_csv(out_dir / "hubspot_webinar_import.csv", [{
        "Email": p.email, "First Name": p.first_name, "Last Name": p.last_name, "Company": p.company,
        "Job Title": p.job_title, "Webinar Name": policy["webinar_name"], "Webinar Registered": "Yes" if p.registered else "No",
        "Webinar Attended": "Yes" if p.attended else "No", "Webinar Watch Type": p.session_type,
        "Webinar Minutes Watched": p.minutes, "Webinar Percent Watched": p.pct_watched,
        "Webinar Poll Answer": p.poll_answer, "Webinar Question": " | ".join(p.questions),
        "Webinar Engagement Score": p.score, "Webinar Follow Up Segment": p.segment,
        "Original Source Drill-Down": p.utm_source} for p in importable],
        ["Email", "First Name", "Last Name", "Company", "Job Title", "Webinar Name", "Webinar Registered",
         "Webinar Attended", "Webinar Watch Type", "Webinar Minutes Watched", "Webinar Percent Watched",
         "Webinar Poll Answer", "Webinar Question", "Webinar Engagement Score", "Webinar Follow Up Segment",
         "Original Source Drill-Down"])
    write_csv(out_dir / "sdr_call_list.csv", [{
        "Rank": i + 1, "Name": p.name, "Email": p.email, "Company": p.company, "Title": p.job_title,
        "Score": p.score, "Why": "; ".join(p.score_reasons), "Call By": p.call_by, "Opener": p.opener,
        "Flags": "; ".join(p.flags)} for i, p in enumerate(hot)],
        ["Rank", "Name", "Email", "Company", "Title", "Score", "Why", "Call By", "Opener", "Flags"])
    write_csv(out_dir / "review_queue.csv", review, ["email", "name", "reason", "detail"])
    write_csv(out_dir / "quarantine.csv", [{"email": p.email, "name": p.name, "company": p.company,
                                             "reason": p.action} for p in ordered if p.segment == QUARANTINE],
              ["email", "name", "company", "reason"])
    write_csv(out_dir / "follow_up_plan.csv", [{
        "email": p.email, "name": p.name, "segment": p.segment, "score": p.score, "action": p.action,
        "owner": p.crm_owner or p.account_owner, "flags": "; ".join(p.flags)} for p in ordered],
        ["email", "name", "segment", "score", "action", "owner", "flags"])
    top_q = next((q for p in ordered if p.segment != QUARANTINE for q in p.questions), "how this works in your CRM")
    emails = ["# Follow-up email drafts", "", "Drafts only. Review, add links, and send from your marketing tool.", ""]
    for seg, (subj, body) in SEGMENT_EMAILS.items():
        who = [p for p in ordered if p.segment == seg]
        if not who:
            continue
        emails += [f"## {seg} ({len(who)})", "", "Recipients: " + ", ".join(p.email for p in who), "",
                   "Subject: " + subj.format(webinar=policy["webinar_name"]), "",
                   body.format(first="{first name}", webinar=policy["webinar_name"], top_q=top_q.rstrip("?") + "?"), ""]
    (out_dir / "follow_up_emails.md").write_text("\n".join(emails))
    (out_dir / "webinar_brief.md").write_text(brief)
    return {"people": ordered, "review": review, "hot": hot, "importable": importable, "used_claude": used_claude}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Turn webinar exports into a follow-up plan.")
    ap.add_argument("--registrants", default=str(HERE / "sample_registrants.csv"))
    ap.add_argument("--attendance", default=str(HERE / "sample_attendance.csv"))
    ap.add_argument("--crm", default=str(HERE / "sample_crm_contacts.csv"))
    ap.add_argument("--policy", default=str(HERE / "webinar_policy.json"))
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-claude", action="store_true", help="Skip Claude even if a key is set.")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    mode = "Claude for openers and brief" if os.getenv("ANTHROPIC_API_KEY") and not args.no_claude else "rules only (no API key)"
    res = run(Path(args.registrants), Path(args.attendance), Path(args.crm), Path(args.policy), Path(args.out),
              use_claude=not args.no_claude)
    people = res["people"]
    print(f"People after merge: {len(people)}. Mode: {mode}")
    for p in people:
        extra = ", ".join(p.flags) if p.flags else ""
        print(f"  {p.score:>3}  {p.segment:<18} {p.name[:22]:<22} {p.company[:20]:<20} {extra}")
    print(f"SDR calls due: {len(res['hot'])}. Review queue: {len(res['review'])}. "
          f"Rows ready to import: {len(res['importable'])}.")
    if mode.startswith("Claude") and not res["used_claude"]:
        print("Claude was not used; outputs come from the built-in templates.")
    print(f"Outputs written to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
