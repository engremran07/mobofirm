"""Accounts created by the platform seed.

Every seeded account has an unusable password and a reserved ``.invalid`` address,
so none of them can sign in or receive mail. The superuser is never created here.
"""

from __future__ import annotations

from dataclasses import dataclass

EMAIL_DOMAIN = "seed.mobofirm.invalid"

UPLOADER_USERNAME = "mobofirm-uploader"
REVIEWER_USERNAME = "mobofirm-reviewer"
EDITORIAL_USERNAME = "mobofirm-editorial"
PUBLISHER_USERNAME = "mobofirm-editor"

FIRMWARE_REVIEWERS_GROUP = "Firmware reviewers"
EDITORS_GROUP = "Editors"


@dataclass(frozen=True)
class PersonSeed:
    username: str
    full_name: str
    bio: str = ""
    is_staff: bool = False
    role: str = ""
    groups: tuple[str, ...] = ()
    can_create_blog_posts: bool = False

    @property
    def email(self) -> str:
        return f"{self.username}@{EMAIL_DOMAIN}"


STAFF_AND_SYSTEM: tuple[PersonSeed, ...] = (
    PersonSeed(
        UPLOADER_USERNAME,
        "Firmware Submissions Desk",
        bio="Submits firmware download references for staff review.",
    ),
    PersonSeed(
        REVIEWER_USERNAME,
        "Firmware Review Desk",
        bio="Reviews and approves firmware listings.",
        is_staff=True,
        role="moderator",
        groups=(FIRMWARE_REVIEWERS_GROUP,),
    ),
    PersonSeed(
        EDITORIAL_USERNAME,
        "Mobofirm Editorial Team",
        bio="Writes the Mobofirm guides and explainers.",
        role="author",
        can_create_blog_posts=True,
    ),
    PersonSeed(
        PUBLISHER_USERNAME,
        "Mobofirm Publishing Desk",
        bio="Reviews and publishes articles and flashing guides.",
        is_staff=True,
        role="editor",
        groups=(EDITORS_GROUP,),
    ),
)

READERS: tuple[PersonSeed, ...] = (
    PersonSeed("reader-hamza-raza", "Hamza Raza"),
    PersonSeed("reader-priya-nair", "Priya Nair"),
    PersonSeed("reader-lucas-meyer", "Lucas Meyer"),
    PersonSeed("reader-amina-yusuf", "Amina Yusuf"),
    PersonSeed("reader-diego-fernandez", "Diego Fernandez"),
    PersonSeed("reader-sana-malik", "Sana Malik"),
    PersonSeed("reader-tomasz-kowalski", "Tomasz Kowalski"),
    PersonSeed("reader-mei-lin", "Mei Lin"),
)

ALL_PEOPLE: tuple[PersonSeed, ...] = (*STAFF_AND_SYSTEM, *READERS)
