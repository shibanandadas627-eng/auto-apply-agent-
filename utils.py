"""Small helpers shared by the web app. Nothing here touches the disk."""

DEFAULT_PROFILE = {
    "name": "", "location": "",
    "roles": "", "must_have": "",
    "avoid": "senior, staff, principal, manager, director",
    "locations": "", "remote_ok": True,
    "job_type": "any",            # any | internship | full-time
    "min_score": 60, "max_per_run": 15,
    "sources": ["remotive", "arbeitnow", "remoteok"],
    "greenhouse": "", "lever": "",
}


def csv(text):
    return [x.strip().lower() for x in str(text or "").split(",") if x.strip()]
