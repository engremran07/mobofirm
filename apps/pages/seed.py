"""Original starter content for the site's information and legal pages.

The text describes only features that exist in this codebase (catalogue, staff review,
download gate, accounts, file manager, offline orders, seller listings). The legal pages
are drafts: they must be reviewed by qualified counsel before a production launch (see
docs/01-product/cms-pages.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import Page

SITE_TOKEN = "[[site_name]]"
EMAIL_TOKEN = "[[contact_email]]"


@dataclass(frozen=True)
class SeedPage:
    slug: str
    title: str
    description: str
    priority: Decimal
    body: str


ABOUT = f"""
{SITE_TOKEN} is a library of mobile device firmware records. Every entry is tied to a
brand, a device model and, where it matters, a chipset, so you can check that a file is
meant for your exact device before you download it.

## What you will find here

- **A device catalogue.** Brands, models, model codes and codenames, with the chipset
  that powers each device.
- **Reviewed firmware records.** Files are described by category (official, engineering,
  readback, modified or other) and appear publicly only after a staff member has
  approved them.
- **Tools and guides.** A catalogue of flashing tools and step-by-step guides that link
  back to the devices they apply to.
- **A blog.** Articles about devices, chipsets and flashing, organised by topic.

## How the library is kept accurate

New records go through a review step. Staff check the device, the category and the file
source, and can reject or withdraw a record at any time. If you find an error, tell us
using the details on the [contact page](/pages/contact/) and we will correct it.

## Who runs it

{SITE_TOKEN} is operated by its site administrators. Accounts are optional for browsing
and required for features that belong to a person, such as the file manager, orders and
seller tools.
"""

CONTACT = f"""
The quickest way to reach the {SITE_TOKEN} team is by email:
**[{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN})**.

## What to write about

- **A wrong or missing record.** Include the brand, the model name or code, and a link to
  the page you are reporting.
- **A broken download link.** Include the firmware page link and what you saw.
- **A takedown request.** Please read the [DMCA and takedown page](/pages/dmca/) first so
  your message contains everything we need.
- **Your account.** For sign-in, verification or two-factor problems, use the account
  pages first. Write to us if you are still locked out.
- **Orders and seller questions.** Mention your username and the order or listing number.

## What to expect

We try to read every message, but we do not promise a response time. Please do not send
passwords, recovery codes or identity documents by email; we will never ask for them.
"""

PRIVACY = f"""
This page explains what personal data {SITE_TOKEN} handles and why. It describes how the
site works today; if that changes, this page changes with it.

## Data we handle

- **Account data.** Email address, username, password (stored only as a salted hash) and
  the profile details you choose to add. Sessions need your email address to be verified.
- **Security data.** Sign-in attempts, two-factor settings and, if device registration
  applies to your account, a random cookie that lets us recognise a registered browser
  together with its browser family and operating system.
- **Downloads.** When you start a firmware download we record the time, the file, your
  IP address and browser identifier so we can enforce limits and detect abuse.
- **Content you create.** Comments, blog posts, marketplace listings and orders.
- **Files you upload.** Files in the file manager belong to your account. They are
  private unless you create a share link, and deleted files are removed after a
  retention period.
- **Server logs.** Routine logs of requests and errors, kept for a limited time.

## Why we use it

To run your account, deliver downloads, keep the service secure, moderate content and
fulfil orders. We do not sell personal data.

## Cookies

See the [cookies and consent page](/pages/consent/) for the cookies the site sets.

## Your choices

You can ask us to access, correct or delete your personal data by writing to
[{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN}). Some records, such as security logs and order
history, may be kept where the law or abuse prevention requires it.

## Retention

Security and device events are kept for a limited, configured period. Server logs rotate
daily and are removed after a short retention window. Account data is kept while the
account exists.

## Contact

Privacy questions: [{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN}).
"""

TERMS = f"""
By using {SITE_TOKEN} you agree to these terms. If you do not agree, please do not use
the service.

## The service

{SITE_TOKEN} publishes information about devices and links to firmware files. Staff review
records, but the library is provided as it is. Read the
[firmware disclaimer](/pages/firmware-disclaimer/) before flashing any device.

## Accounts

You are responsible for your account and for keeping your credentials private. Give
accurate information and verify your email address. We may limit or close accounts that
break these terms or put the service at risk.

## Acceptable use

Do not use the service to break the law, infringe other people's rights, spread malware,
overload or probe the service, or bypass its download limits or review steps. Do not
upload files you have no right to store or share.

## Downloads, orders and packages

Some downloads require you to sign in, wait for a countdown or pass other checks. Download
allowances can depend on the package assigned to your account. Online payments are not
available: an order is a request, and our team contacts you with payment instructions and
activates your purchase only after payment is confirmed.

## Marketplace listings

Sellers are responsible for their listings. Listings are reviewed before they appear and
can be removed. The site does not process payments between buyers and sellers.

## Your files and content

You keep ownership of what you upload or write. You allow us to store and display it as
needed to run the service. Share links give anyone who has the link access to the file
until the link expires or you revoke it.

## Intellectual property

Brand and product names belong to their owners and are used only to identify devices.
Report suspected infringement through the [DMCA and takedown page](/pages/dmca/).

## No warranty and limits of liability

To the extent the law allows, the service and everything in it are provided without
warranties of any kind, and we are not liable for loss or damage caused by using firmware
or tools from the library, including a device that stops working.

## Changes

We may update these terms. Continuing to use the service after a change means you accept
the updated terms.

## Contact

Questions about these terms: [{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN}).
"""

CONSENT = f"""
This page lists the cookies and similar browser storage {SITE_TOKEN} uses.

## Strictly necessary

These keep the site working and secure. They are set without asking because the site cannot
function without them.

| Name | Purpose |
| --- | --- |
| Session cookie | Keeps you signed in and carries one-time messages |
| CSRF cookie | Protects forms and requests against cross-site forgery |
| Device cookie | Recognises a registered browser for account security |

## Preferences

The theme you choose (light or dark) is stored in your browser's local storage so the page
renders in the right theme straight away. It never leaves your browser.

## Analytics and advertising

The site does not set analytics cookies of its own. Pages can include advertising slots.
If an advertising provider that sets cookies is enabled, this page and your consent choices
will be updated before it is used.

## Your choices

You can clear cookies and local storage in your browser settings at any time. Clearing the
session cookie signs you out, and clearing the device cookie means a registered browser
may need to be registered again.
"""

DMCA = f"""
{SITE_TOKEN} respects intellectual property rights and removes material that infringes
them when it is properly reported.

## Sending a notice

Email **[{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN})** with the subject "Takedown notice" and
include:

1. Your name, address, telephone number and email address.
2. A description of the work you say has been infringed, with a link to an authorised
   copy if one exists.
3. The exact web address of each page or file you want removed.
4. A statement that you believe in good faith that the use is not authorised by the
   rights holder, its agent or the law.
5. A statement that the information in your notice is accurate and, under penalty of
   perjury, that you are the rights holder or authorised to act for the rights holder.
6. Your physical or electronic signature.

## What happens next

Staff review the notice. If it is complete, we disable access to the material and may tell
the person who published it. Notices that are incomplete, or that do not identify
material on this site, may not be actioned.

## Counter-notices

If you believe material was removed by mistake, write to the same address with the
removed material's address, a statement under penalty of perjury that you believe the
removal was a mistake, and your contact details and signature.

## Repeat infringement

Accounts that repeatedly publish infringing material may be closed.
"""

DISCLAIMER = f"""
Flashing firmware changes the software on a device. Read this page before you do it.

## Risks

- A wrong, damaged or mismatched file can leave a device unable to start.
- Flashing can erase your data, reset security locks and void manufacturer warranties.
- Some devices block downgrades. A newer security patch level can make older firmware
  unusable on that device.

## How we describe files

Records are grouped by category:

- **Official** firmware comes from the manufacturer or an official channel.
- **Engineering** builds are pre-release or factory builds not meant for end users.
- **Readback** files are dumps read from a device.
- **Modified** files have been changed from the original.
- **Other** files do not fit these groups.

A category describes how we classify a file, not how safe it is. Modified, engineering
and readback files carry extra risk.

## What staff check

Staff review the device match, the category and the file source before a record is public.
They cannot test every file on every device and do not guarantee that a file is free of
defects or malware.

## Your responsibility

Confirm the exact model code, region and variant, compare the checksum shown on the
record when one is available, back up your data, and follow the maker's guidance. See
[how to flash](/pages/how-to-flash/) for a general checklist.

## No affiliation

Brand and product names are used only to identify devices. {SITE_TOKEN} is not affiliated
with or endorsed by the manufacturers listed. Links to outside sites are provided for
convenience and we do not control their content.
"""

HOW_TO_FLASH = """
This is a general checklist. Device makers use different tools and steps, so always follow
the guide for your exact model.

## Before you start

1. **Identify your device.** Find the model code in the phone's settings under *About*.
   Search for it in the [catalogue](/firmwares/browse/) to confirm the exact record.
2. **Back up your data.** Flashing can erase everything on the device.
3. **Charge the battery.** Start with at least 60% so the device does not switch off
   midway.
4. **Check the account locks.** Remove or know the credentials for factory-reset
   protection and any screen or cloud lock.
5. **Read the firmware page.** Note the category, region, version and checksum, and read
   the [disclaimer](/pages/firmware-disclaimer/).

## Choose the right tool

Different chipsets and brands use different flashing tools. The
[tools and guides](/firmwares/tools/) section lists the tools in our catalogue with their
platforms and risk levels, and the [guides](/firmwares/guides/) walk through specific
devices.

## Flash

1. Download the file and, when a checksum is shown, compare it with the file you
   downloaded.
2. Unpack the archive if the guide says so.
3. Connect the device in the mode the tool expects, using a good cable and a direct port.
4. Load the files exactly as the guide describes and start the process.
5. Do not unplug the device or interrupt the computer until the tool reports success.

## After flashing

The first start can take several minutes. If the device does not start, do not repeat the
process blindly: check that the file matches your exact model and variant, then consult the
guide for your tool.
"""

FAQ = """
## Is browsing free?

Yes. You can search the catalogue and read records without an account. Some downloads may
need you to sign in and verify your email address.

## Why do downloads have a countdown?

Downloads are gated on the server to limit automated traffic. The countdown, any ad step
and the single-use download link are all enforced by the server, not just shown on screen.

## Who approves the firmware listed here?

Staff review each record before it appears publicly and can withdraw it later. See the
[firmware disclaimer](/pages/firmware-disclaimer/) for what review does and does not cover.

## What do the categories mean?

Official, engineering, readback, modified and other describe how a file is classified. The
disclaimer page explains each one.

## My device is missing. What can I do?

Write to us using the [contact page](/pages/contact/) with the brand, model name and model
code, and we will look at adding it.

## Can I buy a larger download allowance?

The [shop](/shop/) lists download packages. Online payment is not available: you place an
order request, our team contacts you with payment instructions, and the package is
activated once payment is confirmed.

## What is the file manager?

A private storage area for signed-in users with folders, a storage quota, share links with
expiry and trash. Files are private unless you share them.

## Why was my browser asked to register?

Some accounts limit the number of approved browsers for security. See your account's device
page for the current allowance.

## How do I report an error or a copyright problem?

For errors, use the [contact page](/pages/contact/). For copyright, follow the
[takedown instructions](/pages/dmca/).
"""

ADVERTISE = f"""
{SITE_TOKEN} reaches people who are looking for device information and flashing guidance.
We are open to direct partnerships from companies whose products suit that audience, such
as tools, accessories and repair services.

## Principles

- Advertising is always separated from editorial content and from firmware records.
- We do not accept advertising that promotes malware, piracy or deceptive downloads.
- Firmware records and their review are never influenced by advertisers.

## How to enquire

Write to **[{EMAIL_TOKEN}](mailto:{EMAIL_TOKEN})** with your company, the product you want
to promote and the markets you serve. We will reply if there is a fit. There is no
self-service advertising purchase on the site.
"""

SEED_PAGES: tuple[SeedPage, ...] = (
    SeedPage(
        "about",
        "About",
        "What the firmware library covers and how its records are reviewed.",
        Decimal("0.8"),
        ABOUT,
    ),
    SeedPage(
        "contact",
        "Contact",
        "How to reach the team about records, downloads, accounts and takedowns.",
        Decimal("0.6"),
        CONTACT,
    ),
    SeedPage(
        "privacy",
        "Privacy policy",
        "What personal data the site handles, why, and your choices.",
        Decimal("0.3"),
        PRIVACY,
    ),
    SeedPage(
        "terms",
        "Terms of service",
        "The rules for using the site, accounts, orders and listings.",
        Decimal("0.3"),
        TERMS,
    ),
    SeedPage(
        "consent",
        "Cookies and consent",
        "The cookies and browser storage the site uses.",
        Decimal("0.3"),
        CONSENT,
    ),
    SeedPage(
        "dmca",
        "DMCA and takedown",
        "How to report material that infringes your rights.",
        Decimal("0.3"),
        DMCA,
    ),
    SeedPage(
        "firmware-disclaimer",
        "Firmware disclaimer and safety",
        "Risks of flashing firmware and what the file categories mean.",
        Decimal("0.7"),
        DISCLAIMER,
    ),
    SeedPage(
        "how-to-flash",
        "How to flash firmware: an overview",
        "A general checklist for preparing, flashing and recovering a device.",
        Decimal("0.8"),
        HOW_TO_FLASH,
    ),
    SeedPage(
        "faq",
        "Frequently asked questions",
        "Answers about downloads, review, packages and accounts.",
        Decimal("0.7"),
        FAQ,
    ),
    SeedPage(
        "advertise",
        "Advertise",
        "Partnership principles and how to enquire about advertising.",
        Decimal("0.4"),
        ADVERTISE,
    ),
)


@dataclass(frozen=True)
class SeedReport:
    created: int
    updated: int
    unchanged: int


def render_body(page: SeedPage, *, site_name: str, contact_email: str) -> str:
    return (
        page.body.replace(SITE_TOKEN, site_name)
        .replace(EMAIL_TOKEN, contact_email)
        .strip()
        + "\n"
    )


@transaction.atomic
def seed_pages(
    *, site_name: str, contact_email: str, force: bool = False
) -> SeedReport:
    """Publish the starter pages. Existing pages are kept unless ``force`` is set.

    Idempotent: a second run creates nothing and leaves staff edits alone. ``force``
    rewrites the seeded fields of existing pages and is the only way to refresh them.
    """
    created = updated = unchanged = 0
    for seed in SEED_PAGES:
        content = render_body(seed, site_name=site_name, contact_email=contact_email)
        fields = {
            "title": seed.title,
            "content": content,
            "content_format": Page.ContentFormat.MARKDOWN,
            "status": Page.Status.PUBLISHED,
            "access_level": Page.Access.PUBLIC,
            "seo_description": seed.description,
            "include_in_sitemap": True,
            "priority": seed.priority,
        }
        page = Page.objects.filter(slug=seed.slug).first()
        if page is None:
            Page.objects.create(slug=seed.slug, **fields)
            created += 1
        elif force and any(
            getattr(page, key) != value for key, value in fields.items()
        ):
            for key, value in fields.items():
                setattr(page, key, value)
            page.save()
            updated += 1
        else:
            unchanged += 1
    return SeedReport(created, updated, unchanged)
