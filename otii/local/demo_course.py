"""A small public demo course in the Otii organisation, for trying Otii Learn on a laptop.

    otii/local/demo-course.sh            create it (skipped if it already exists)
    otii/local/demo-course.sh --remove   delete it

Signs in as the install's first admin through LearnHouse's own API. The course
is found again by the extra_metadata marker below, never by name.
"""

from __future__ import annotations

import os
import sys

import httpx

MARKER = {"otii_local_demo": True}
NAME = "Welcome to Otii Learn"
PAGES = [
    ("Why training matters", "Good training keeps children safe and helps every practitioner feel confident."),
    ("What happens next", "When you finish this course, it appears in your Otii Learn log in otii."),
]


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing setting {name}")
    return value


def page(text: str) -> dict:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def main(argv: list[str]) -> None:
    api = f"http://127.0.0.1:{env('LEARN_API_PORT')}/api/v1"
    with httpx.Client(timeout=30) as http:
        login = http.post(
            f"{api}/auth/login",
            data={"username": env("LEARNHOUSE_INITIAL_ADMIN_EMAIL"), "password": env("LEARNHOUSE_INITIAL_ADMIN_PASSWORD")},
        )
        login.raise_for_status()
        body = login.json()
        token = (body.get("tokens") or {}).get("access_token") or body.get("access_token")
        http.headers["Authorization"] = f"Bearer {token}"

        slug = env("OTII_LEARN_ORG_SLUG")
        org = http.get(f"{api}/orgs/slug/{slug}").raise_for_status().json()
        courses = http.get(
            f"{api}/courses/org_slug/{slug}/page/1/limit/100", params={"include_unpublished": "true"}
        ).raise_for_status().json()
        existing = [c for c in courses if (c.get("extra_metadata") or {}).get("otii_local_demo")]

        if "--remove" in argv:
            for course in existing:
                http.delete(f"{api}/courses/{course['course_uuid']}").raise_for_status()
            print(f"demo course: removed {len(existing)}")
            return
        if existing:
            print(f"demo course: already present ({existing[0]['course_uuid']})")
            return

        course = http.post(
            f"{api}/courses/",
            params={"org_id": org["id"]},
            data={"name": NAME, "description": "A two-page demo course.", "about": "A short demo course.", "public": "true"},
        ).raise_for_status().json()
        chapter = http.post(
            f"{api}/chapters/",
            json={"name": "Getting started", "org_id": org["id"], "course_id": course["id"]},
        ).raise_for_status().json()
        for title, text in PAGES:
            http.post(
                f"{api}/activities/",
                json={
                    "name": title,
                    "chapter_id": chapter["id"],
                    "activity_type": "TYPE_DYNAMIC",
                    "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE",
                    "content": page(text),
                    "published": True,
                },
            ).raise_for_status()
        http.put(
            f"{api}/courses/{course['course_uuid']}",
            json={"public": True, "published": True, "extra_metadata": MARKER},
        ).raise_for_status()
        print(f"demo course: created {course['course_uuid']}")


if __name__ == "__main__":
    main(sys.argv[1:])
