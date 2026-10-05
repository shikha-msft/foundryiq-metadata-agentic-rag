"""Generate the synthetic sample library used by this POC.

Contoso is a fictional company. Every review, service, team and person here is invented.

The data is built to reproduce the real-world problem:
  - Narratives describe what happened in plain language and rarely use the category
    names. A configuration-change incident says "a gateway routing rule was updated",
    not "configuration change".
  - Some narratives mention category words misleadingly ("the team first suspected a
    recent configuration change, but ..."), the way real write-ups do.
So keyword and vector search over the text alone find the wrong reviews, while the
metadata columns say exactly what each review is about.

Outputs (in this folder):
  post_incident_reviews.xlsx   one row per review: the SharePoint library export
  pdfs/*.pdf                   one PDF per review: the documents in the library

Usage: python generate_sample_data.py
"""
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import ListFlowable, Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).parent
N_REVIEWS = 64
random.seed(20260921)

SERVICES = {
    "Payments API": "Payments Platform",
    "Identity Platform": "Identity and Access",
    "Customer Portal": "Digital Channels",
    "Data Warehouse": "Data Platform",
    "Notification Service": "Messaging",
    "Search Service": "Digital Channels",
    "Mobile Backend": "Mobile Engineering",
    "Order Management": "Commerce Platform",
}

SYMPTOMS = ["elevated error rates", "degraded latency", "partial outage", "failed sign-ins",
            "delayed processing", "intermittent timeouts", "stale data shown to users"]

# Root cause -> plain-language narratives that avoid the category name
ROOT_CAUSES = {
    "Configuration change": [
        "a gateway routing rule was updated to send traffic to a new backend pool that had not been warmed up",
        "the TLS cipher list was tightened as part of a hardening rollout, and older clients could no longer negotiate a connection",
        "the connection pool size in a shared deployment template was lowered from 200 to 20",
        "a feature flag was switched on for every tenant instead of the pilot group",
        "a DNS record was repointed during a migration while the old target was still serving writes",
        "a pipeline overwrote the environment variable that pointed the service at its cache cluster",
    ],
    "Capacity": [
        "request volume during a marketing campaign exceeded the provisioned throughput",
        "the database ran out of connections under end-of-month batch load",
        "the disk on the primary node filled with transaction logs",
        "the autoscaler reached its maximum instance count during a traffic peak",
        "one tenant's bulk import consumed most of the shared queue throughput",
    ],
    "Dependency failure": [
        "an upstream identity provider returned intermittent timeouts",
        "a third-party SMS gateway degraded without notice",
        "the managed message broker in the region had a partial outage",
        "an external payment processor changed its response format without warning",
    ],
    "Software defect": [
        "a null reference in the new release crashed workers on a rare input",
        "a retry loop without backoff multiplied the load on a struggling downstream call",
        "a race condition in the session cache returned tokens belonging to expired sessions",
        "a schema migration took a long lock on a heavily used table",
    ],
    "Certificate expiry": [
        "the client certificate used for the partner integration was not renewed",
        "a certificate on the internal API gateway lapsed over a weekend",
        "the token signing certificate reached its end date",
    ],
}
ROOT_CAUSE_WEIGHTS = {"Configuration change": 5, "Capacity": 4, "Dependency failure": 3,
                      "Software defect": 4, "Certificate expiry": 2}

DETECTION = {
    "Customer report": ["The first signal was a spike in support tickets from merchants.",
                        "A partner raised a case with the service desk before any internal signal fired.",
                        "Users posted about the problem on social media, and the support team escalated it."],
    "Synthetic monitoring": ["A scripted sign-in probe started failing from two regions.",
                             "The end-to-end checkout probe went red."],
    "Alerting": ["The error-rate alert paged the on-call engineer.",
                 "A latency threshold alert fired for the service's main endpoint."],
    "Internal user": ["A developer in another team noticed their integration tests failing against the service.",
                      "Staff in the contact centre noticed the console was slow and told the service owner."],
}
DETECTION_WEIGHTS = {"Customer report": 4, "Synthetic monitoring": 3, "Alerting": 4, "Internal user": 2}

FACTORS = {
    "Alert fatigue": "The relevant alert had fired repeatedly the previous week and had been muted by the on-call engineer.",
    "Missing runbook": "There was no documented procedure for this failure mode, so responders worked it out live.",
    "Untested rollback": "The rollback script had never been exercised and failed on first use.",
    "Single point of failure": "All traffic depended on one component with no standby.",
    "Insufficient load testing": "The expected load had not been tested before release.",
    "Change outside window": "The change was made outside the approved maintenance window.",
    "Unclear ownership": "It took time to work out which team owned the affected component.",
}

# Misleading mentions of category words, the way real write-ups include them
DISTRACTORS = [
    "The team first suspected a recent configuration change, but the change log showed nothing relevant.",
    "Customer impact was limited because most traffic was served from cache.",
    "Capacity dashboards were checked early and showed normal headroom.",
    "Certificates were checked as part of triage and were valid.",
    "No dependency outages were reported by upstream providers during the window.",
]

ACTIONS = [
    "Add a pre-deployment check that validates the setting against the production baseline.",
    "Write and rehearse a runbook for this failure mode.",
    "Add a synthetic probe that exercises the affected path every minute.",
    "Load test the service at twice the expected peak before the next campaign.",
    "Move certificate renewal into the automated rotation pipeline.",
    "Add a circuit breaker and timeout on the upstream call.",
    "Review alert thresholds so the alert only fires when action is needed.",
    "Exercise the rollback path in staging as part of every release.",
    "Assign a named owner for the component in the service catalogue.",
]

REGIONS = ["AU East", "AU Southeast", "Southeast Asia", "US West"]


def pick_weighted(weights):
    keys = list(weights)
    return random.choices(keys, weights=[weights[k] for k in keys])[0]


def make_review(i):
    service = random.choice(list(SERVICES))
    cause = pick_weighted(ROOT_CAUSE_WEIGHTS)
    detection = pick_weighted(DETECTION_WEIGHTS)
    factors = random.sample(list(FACTORS), k=random.choice([1, 2, 2, 3]))
    severity = random.choices(["Sev1", "Sev2", "Sev3", "Sev4"], weights=[2, 4, 5, 2])[0]
    environment = random.choices(["Production", "Staging"], weights=[6, 1])[0]
    region = random.choice(REGIONS)
    symptom = random.choice(SYMPTOMS)
    when = date(2025, 1, 6) + timedelta(days=random.randint(0, 560))
    duration = random.choice([12, 18, 25, 40, 55, 75, 110, 160, 240])
    change_related = "Yes" if cause in ("Configuration change", "Software defect") else random.choice(["No", "No", "Yes"])
    ident = f"PIR-{when.year}-{i:03d}"
    title = f"{service}: {symptom}"

    cause_text = random.choice(ROOT_CAUSES[cause])
    story = [
        f"On {when:%d %B %Y}, {service} experienced {symptom} in {environment.lower()} ({region}) "
        f"for about {duration} minutes. {DETECTION[detection][random.randrange(len(DETECTION[detection]))]}",
        f"Investigation found that {cause_text}.",
    ]
    if random.random() < 0.55:
        story.append(random.choice([d for d in DISTRACTORS if cause.split()[0].lower() not in d.lower()
                                    and not (detection == "Customer report" and d.startswith("Customer"))]))
    story += [FACTORS[f] for f in factors]
    summary = " ".join(story[:2])

    timeline = [
        f"{t:02d}:{m:02d} {e}" for t, m, e in [
            (9, random.randint(0, 20), "First signal received."),
            (9, random.randint(21, 40), "Incident declared and on-call team engaged."),
            (9, random.randint(41, 59), "Likely cause identified."),
            (10, random.randint(0, 30), "Mitigation applied."),
            (10, random.randint(31, 59), "Service confirmed healthy and incident closed."),
        ]]
    actions = random.sample(ACTIONS, k=3)

    return {
        "ID": 1000 + i,
        "Name": f"{ident} {service} {symptom}.pdf".replace(":", ""),
        "Incident ID": ident,
        "Incident Date": when.isoformat(),
        "Title": title,
        "Service": service,
        "Owning Team": SERVICES[service],
        "Severity": severity,
        "Root Cause Category": cause,
        "Contributing Factors": "; ".join(factors),
        "Detection Method": detection,
        "Change Related": change_related,
        "Environment": environment,
        "Region": region,
        "Summary": summary,
        "Path": "sites/Engineering/Post Incident Reviews",
        "_story": story, "_timeline": timeline, "_actions": actions, "_duration": duration,
    }


def write_pdf(review, folder):
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    doc = SimpleDocTemplate(str(folder / review["Name"]), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=review["Title"], author="Contoso Platform Engineering (fictional)")
    bullets = lambda items: ListFlowable([Paragraph(x, body) for x in items], bulletType="bullet")
    flow = [
        Paragraph(f"Post-incident review {review['Incident ID']}", styles["Title"]),
        Paragraph(review["Title"], styles["Heading2"]),
        Paragraph("Contoso Platform Engineering. Fictional sample data.", styles["Italic"]),
        Spacer(1, 10),
        Paragraph("Summary", styles["Heading3"]), Paragraph(" ".join(review["_story"][:2]), body),
        Paragraph("Impact", styles["Heading3"]),
        Paragraph(f"Users of {review['Service']} saw {review['Title'].split(': ')[1]} for about "
                  f"{review['_duration']} minutes. The service owner, {review['Owning Team']}, "
                  f"led the response.", body),
        Paragraph("Timeline", styles["Heading3"]), bullets(review["_timeline"]),
        Paragraph("What happened", styles["Heading3"]),
        *[Paragraph(p, body) for p in review["_story"][1:]],
        Paragraph("What went well", styles["Heading3"]),
        Paragraph("Responders communicated clearly in the incident channel and the status page was "
                  "updated within fifteen minutes of the incident being declared.", body),
        Paragraph("Actions", styles["Heading3"]), bullets(review["_actions"]),
    ]
    doc.build(flow)


def main():
    reviews = [make_review(i) for i in range(1, N_REVIEWS + 1)]
    pdf_dir = HERE / "pdfs"
    pdf_dir.mkdir(exist_ok=True)
    for old in pdf_dir.glob("*.pdf"):
        old.unlink()
    for r in reviews:
        write_pdf(r, pdf_dir)
    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in reviews])
    df.to_excel(HERE / "post_incident_reviews.xlsx", index=False)
    print(f"Wrote {len(reviews)} reviews: post_incident_reviews.xlsx and pdfs/")
    print(df["Root Cause Category"].value_counts().to_string())


if __name__ == "__main__":
    main()
