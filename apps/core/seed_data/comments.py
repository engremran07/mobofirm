"""Reader comments submitted through the comments service by the platform seed.

Comments that stay within the link limit are approved automatically; comments with
many links are queued for staff by the service's own screening, not by a flag here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .people import EDITORIAL_USERNAME


@dataclass(frozen=True)
class CommentSeed:
    key: str
    username: str
    body: str
    article: str = ""
    brand: str = ""
    model: str = ""
    reply_to: str = ""


COMMENTS: tuple[CommentSeed, ...] = (
    CommentSeed(
        "model-code-thanks",
        "reader-priya-nair",
        "Thanks for this. I kept mixing up the marketing name and the model code, "
        "and the adb getprop tip saved me a trip to find the box.",
        article="how-to-find-your-phone-model-code",
    ),
    CommentSeed(
        "model-code-box",
        "reader-diego-fernandez",
        "Does the model number in Settings always match the label on the box? "
        "On my phone the box lists a different code than the About screen.",
        article="how-to-find-your-phone-model-code",
    ),
    CommentSeed(
        "model-code-box-reply",
        EDITORIAL_USERNAME,
        "They can differ. A retail box often lists a regional SKU, while Settings "
        "shows the code the software reports. If they disagree, trust the code "
        "printed on the phone itself and compare it with the manufacturer's "
        "support page.",
        article="how-to-find-your-phone-model-code",
        reply_to="model-code-box",
    ),
    CommentSeed(
        "checksum-source",
        "reader-lucas-meyer",
        "Get-FileHash worked first time. Worth stressing that the reference hash "
        "should come from the publisher and not from the mirror hosting the file.",
        article="how-to-verify-a-firmware-download-with-sha-256",
    ),
    CommentSeed(
        "checksum-missing",
        "reader-sana-malik",
        "What should I do if the manufacturer does not publish a checksum at all?",
        article="how-to-verify-a-firmware-download-with-sha-256",
    ),
    CommentSeed(
        "checksum-missing-reply",
        EDITORIAL_USERNAME,
        "Then you can only confirm that your download is intact, for example by "
        "hashing the file again after copying it. For authenticity, prefer "
        "packages that the phone verifies itself, such as signed OTA updates.",
        article="how-to-verify-a-firmware-download-with-sha-256",
        reply_to="checksum-missing",
    ),
    CommentSeed(
        "unlock-banking",
        "reader-tomasz-kowalski",
        "I unlocked my phone last year and my banking app refused to start until "
        "I locked the bootloader again. Good that the article mentions this.",
        article="bootloader-unlocking-basics-and-risks",
    ),
    CommentSeed(
        "unlock-warranty",
        "reader-amina-yusuf",
        "Does relocking the bootloader bring the warranty back?",
        article="bootloader-unlocking-basics-and-risks",
    ),
    CommentSeed(
        "unlock-warranty-reply",
        EDITORIAL_USERNAME,
        "Not automatically. Policies differ by manufacturer and region, and some "
        "brands record the unlock permanently, as Samsung does with its Knox "
        "warranty flag. Read the warranty terms before you unlock.",
        article="bootloader-unlocking-basics-and-risks",
        reply_to="unlock-warranty",
    ),
    CommentSeed(
        "odin-linux",
        "reader-mei-lin",
        "Heimdall worked for me on Linux with a Galaxy A-series phone once I fixed "
        "the libusb permissions. Odin on Windows is still the smoother option.",
        article="odin-vs-heimdall-for-samsung-firmware",
    ),
    CommentSeed(
        "odin-links",
        "reader-hamza-raza",
        "Useful comparison. Here are the pages I used: "
        "https://glassechidna.com.au/heimdall/ "
        "https://developer.android.com/tools/releases/platform-tools "
        "https://source.android.com/docs/core/architecture/bootloader",
        article="odin-vs-heimdall-for-samsung-firmware",
    ),
    CommentSeed(
        "categories-pixel",
        "reader-sana-malik",
        "Clear explanation. Which category should I use if I only want the stock "
        "build for my Pixel?",
        article="firmware-categories-explained",
    ),
    CommentSeed(
        "categories-pixel-reply",
        EDITORIAL_USERNAME,
        "Official. Those listings point to software published by the "
        "manufacturer. The other categories are listed for reference and carry "
        "more risk.",
        article="firmware-categories-explained",
        reply_to="categories-pixel",
    ),
    CommentSeed(
        "modes-edl",
        "reader-diego-fernandez",
        "EDL mode is the one that worries me. Is it a normal thing to use?",
        article="fastboot-download-mode-edl-and-brom-explained",
    ),
    CommentSeed(
        "modes-edl-reply",
        EDITORIAL_USERNAME,
        "It is a deep recovery path meant for service work. Use it only with the "
        "package and programmer made for your exact device, and stay with "
        "fastboot for everything else.",
        article="fastboot-download-mode-edl-and-brom-explained",
        reply_to="modes-edl",
    ),
    CommentSeed(
        "adb-unauthorized",
        "reader-lucas-meyer",
        "Windows kept reporting the device as unauthorized until I accepted the "
        "RSA prompt on the phone. A line about that would help beginners.",
        article="install-adb-and-fastboot-on-windows-macos-and-linux",
    ),
    CommentSeed(
        "adb-udev-links",
        "reader-priya-nair",
        "For Linux users, these pages covered the udev rules for me: "
        "https://developer.android.com/studio/run/device "
        "https://wiki.archlinux.org/title/Android_Debug_Bridge "
        "https://developer.android.com/tools/adb",
        article="install-adb-and-fastboot-on-windows-macos-and-linux",
    ),
    CommentSeed(
        "rollback-xiaomi",
        "reader-tomasz-kowalski",
        "I learned this the hard way on a Xiaomi phone. Check the anti-rollback "
        "value before you try a downgrade.",
        article="anti-rollback-protection-why-downgrades-can-brick",
    ),
    CommentSeed(
        "rollback-read",
        "reader-amina-yusuf",
        "Is there a way to read the current rollback index from the phone?",
        article="anti-rollback-protection-why-downgrades-can-brick",
    ),
    CommentSeed(
        "rollback-read-reply",
        EDITORIAL_USERNAME,
        "On many devices a Fastboot variable exposes it, for example "
        "fastboot getvar anti on Xiaomi phones, but the name differs by "
        "manufacturer. Check the vendor's documentation for your model.",
        article="anti-rollback-protection-why-downgrades-can-brick",
        reply_to="rollback-read",
    ),
    CommentSeed(
        "external-label",
        "reader-hamza-raza",
        "I appreciate the explicit External download label. Is the list of "
        "allowed hosts public?",
        article="how-mobofirm-lists-external-firmware-downloads",
    ),
    CommentSeed(
        "external-label-reply",
        EDITORIAL_USERNAME,
        "The allow-list is maintained by site staff, and every external listing "
        "is reviewed before it appears. The host is always shown on the download "
        "page before you continue.",
        article="how-mobofirm-lists-external-firmware-downloads",
        reply_to="external-label",
    ),
    CommentSeed(
        "s24-us-variant",
        "reader-mei-lin",
        "Is the US variant of the Galaxy S24 Ultra, SM-S928U, different from the "
        "global SM-S928B build?",
        brand="samsung",
        model="galaxy-s24-ultra",
    ),
    CommentSeed(
        "pixel8-newest",
        "reader-lucas-meyer",
        "Which build is the newest listed for the Pixel 8, and does it include "
        "the latest security patch?",
        brand="google",
        model="pixel-8",
    ),
)
