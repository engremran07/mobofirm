"""Original editorial articles published by the platform seed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategorySeed:
    slug: str
    name: str


@dataclass(frozen=True)
class ArticleSeed:
    slug: str
    title: str
    summary: str
    category: str
    tags: tuple[str, ...]
    body: str
    seo_title: str
    seo_description: str
    featured: bool = False


CATEGORIES: tuple[CategorySeed, ...] = (
    CategorySeed("getting-started", "Getting started"),
    CategorySeed("flashing-and-tools", "Flashing and tools"),
    CategorySeed("security-and-safety", "Security and safety"),
    CategorySeed("hardware-explained", "Hardware explained"),
    CategorySeed("about-the-catalog", "About the catalog"),
)

ARTICLES: tuple[ArticleSeed, ...] = (
    ArticleSeed(
        slug="how-to-find-your-phone-model-code",
        title="How to find your phone's exact model code",
        summary=(
            "The marketing name is not enough to pick firmware. Here is where to "
            "read the real model code, codename and build ID on any Android phone."
        ),
        category="getting-started",
        tags=("Model code", "Firmware basics", "Android"),
        seo_title="How to find your phone's exact model code",
        seo_description=(
            "Find the model code, codename and build ID of an Android phone from "
            "Settings, the device label or ADB before you choose firmware."
        ),
        featured=True,
        body="""
<p>Two phones sold under the same name can carry different hardware, different radios and different software. That is why every firmware download is matched to a <strong>model code</strong>, not to the name on the box. Samsung uses codes such as <code>SM-S928B</code>, Google uses short codes such as <code>GKV4X</code>, and Xiaomi uses strings such as <code>23127PN0CG</code>.</p>
<h2>Where to read it</h2>
<ol>
<li><strong>Settings.</strong> Open Settings, then About phone. The entry called Model number (or Model) usually shows the code. On some brands you have to open a sub-page such as Device information.</li>
<li><strong>The device itself.</strong> Many phones print the code on the SIM tray, on the back plate or under the battery cover. This is the most reliable source because it cannot be changed by software.</li>
<li><strong>The retail box.</strong> The label often lists a regional SKU. It is useful, but it can differ from the code the software reports.</li>
<li><strong>ADB.</strong> With USB debugging enabled, run <code>adb shell getprop ro.product.model</code> for the model code and <code>adb shell getprop ro.product.device</code> for the codename.</li>
<li><strong>Boot screens.</strong> Download mode, fastboot and recovery screens often repeat the model code and the current build.</li>
</ol>
<h2>Check the build too</h2>
<p>The build ID tells you which software the phone runs today. Run <code>adb shell getprop ro.build.display.id</code> or read Build number in About phone. Never flash a package that is older than the one installed without reading our article on anti-rollback protection first.</p>
<h2>Before you download anything</h2>
<ul>
<li>Match the full code, including regional suffixes.</li>
<li>Match the region or sales channel of the package to the phone.</li>
<li>If the code on the phone and the code in the catalog differ in any character, stop and ask the manufacturer or an authorised service centre.</li>
</ul>
""",
    ),
    ArticleSeed(
        slug="how-to-verify-a-firmware-download-with-sha-256",
        title="How to verify a firmware download with SHA-256",
        summary=(
            "A checksum tells you whether the file you received is the file that "
            "was published. Learn to compute and compare one on Windows, macOS and Linux."
        ),
        category="security-and-safety",
        tags=("Checksums", "Security", "Firmware basics"),
        seo_title="Verify a firmware download with SHA-256",
        seo_description=(
            "Compute and compare a SHA-256 checksum on Windows, macOS or Linux to "
            "confirm a firmware file arrived intact and unmodified."
        ),
        body="""
<p>A firmware package is large, and a single flipped bit can leave a phone unable to boot. A cryptographic checksum such as <strong>SHA-256</strong> condenses the whole file into 64 hexadecimal characters. If the checksum you compute matches the one the publisher released, the file you hold is identical to theirs.</p>
<h2>Compute the checksum</h2>
<table>
<thead><tr><th scope="col">System</th><th scope="col">Command</th></tr></thead>
<tbody>
<tr><td>Windows PowerShell</td><td><code>Get-FileHash -Algorithm SHA256 .\\firmware.zip</code></td></tr>
<tr><td>Windows Command Prompt</td><td><code>certutil -hashfile firmware.zip SHA256</code></td></tr>
<tr><td>macOS</td><td><code>shasum -a 256 firmware.zip</code></td></tr>
<tr><td>Linux</td><td><code>sha256sum firmware.zip</code></td></tr>
</tbody>
</table>
<h2>Compare it correctly</h2>
<ul>
<li>Take the reference value from the <strong>publisher</strong>, ideally the manufacturer. A checksum copied from the same mirror that hosts the file proves the download was not corrupted, but it cannot prove the file is genuine.</li>
<li>Compare every character, or paste both values into a text editor and let it highlight differences.</li>
<li>If the values differ, delete the file and download it again. If it differs a second time, do not flash it.</li>
</ul>
<h2>What a checksum cannot tell you</h2>
<p>A matching hash says nothing about whether the package suits your model, region or bootloader state. Always match the model code first, as described in our article on finding your model code.</p>
""",
    ),
    ArticleSeed(
        slug="bootloader-unlocking-basics-and-risks",
        title="Bootloader unlocking: what it does and what it costs you",
        summary=(
            "Unlocking the bootloader is the gateway to custom software, and it "
            "comes with real trade-offs. Understand them before you start."
        ),
        category="flashing-and-tools",
        tags=("Bootloader", "Security", "Flashing"),
        seo_title="Bootloader unlocking: basics and risks",
        seo_description=(
            "What an Android bootloader unlock changes, which data and features it "
            "affects, and the questions to answer before you unlock."
        ),
        featured=True,
        body="""
<p>The bootloader is the first program that runs when a phone starts. A <strong>locked</strong> bootloader only starts software signed by the manufacturer. An <strong>unlocked</strong> one accepts other images, which is what custom recoveries, custom ROMs and some repair workflows need.</p>
<h2>What unlocking changes</h2>
<ul>
<li><strong>Your data is erased.</strong> Unlocking performs a factory reset by design, so take a full backup first.</li>
<li><strong>Verified boot reports a changed state.</strong> The phone shows a warning at start-up, and apps that rely on device integrity checks can stop working until the bootloader is locked again.</li>
<li><strong>Some features can degrade.</strong> Depending on the manufacturer, hardware-backed DRM, certain camera processing or payment features may be limited on an unlocked device.</li>
<li><strong>Warranty terms may change.</strong> Some manufacturers record the unlock permanently. Read the warranty conditions for your region before you proceed.</li>
</ul>
<h2>The usual process</h2>
<ol>
<li>Enable Developer options by tapping Build number seven times.</li>
<li>Turn on OEM unlocking. If the switch is greyed out, the device or its carrier does not allow unlocking.</li>
<li>Boot to fastboot mode and run the manufacturer's unlock command, for example <code>fastboot flashing unlock</code>. Some brands, such as Xiaomi, require their own unlock tool, a linked account and a waiting period.</li>
<li>Confirm on the phone screen. The device resets and restarts.</li>
</ol>
<h2>Ask yourself first</h2>
<p>Do you need an unlocked bootloader at all? Restoring official firmware to a working phone often does not require one. If the answer is yes, follow the manufacturer's own instructions and keep a copy of the official firmware for your exact model code so you can return to stock.</p>
""",
    ),
    ArticleSeed(
        slug="odin-vs-heimdall-for-samsung-firmware",
        title="Odin vs Heimdall: choosing a tool for Samsung firmware",
        summary=(
            "Both tools talk to a Galaxy phone in Download mode. This comparison "
            "covers platform support, reliability and when each one makes sense."
        ),
        category="flashing-and-tools",
        tags=("Odin", "Heimdall", "Samsung"),
        seo_title="Odin vs Heimdall for Samsung firmware",
        seo_description=(
            "Compare Odin and Heimdall for flashing Samsung Galaxy firmware: "
            "platforms, package support, risks and when to use each tool."
        ),
        body="""
<p>Samsung phones are flashed in <strong>Download mode</strong> using firmware split into four files: <code>AP</code> (system and apps), <code>BL</code> (bootloader), <code>CP</code> (modem) and <code>CSC</code> (region and carrier settings). Two tools can load them.</p>
<h2>Odin</h2>
<ul>
<li>Runs on Windows only.</li>
<li>Has a dedicated slot for each of the four package files and understands the <code>.tar.md5</code> format directly.</li>
<li>Is Samsung's own tool, but Samsung does not distribute it publicly, so copies come from the community. Verify the source and the file before running it.</li>
</ul>
<h2>Heimdall</h2>
<ul>
<li>Open source and available for Windows, macOS and Linux.</li>
<li>Flashes individual partition images with the same protocol Odin uses. You normally unpack the package first.</li>
<li>Depends on libusb and correct USB permissions, which can take some effort to set up.</li>
</ul>
<h2>Which should you pick?</h2>
<table>
<thead><tr><th scope="col">Situation</th><th scope="col">Better choice</th></tr></thead>
<tbody>
<tr><td>You use Windows and have a complete AP, BL, CP and CSC package</td><td>Odin</td></tr>
<tr><td>You use Linux or macOS</td><td>Heimdall</td></tr>
<tr><td>You need to flash a single partition image</td><td>Heimdall</td></tr>
<tr><td>You want the most widely documented workflow</td><td>Odin</td></tr>
</tbody>
</table>
<p>Whichever you choose, match the model code and region exactly, and use <strong>HOME_CSC</strong> rather than CSC when you want to keep your data.</p>
""",
    ),
    ArticleSeed(
        slug="firmware-categories-explained",
        title="Official, engineering, readback, modified: firmware categories explained",
        summary=(
            "Every firmware listing on Mobofirm sits in a category that describes "
            "its origin. Here is what each one means and how much trust it deserves."
        ),
        category="about-the-catalog",
        tags=("Firmware categories", "Firmware basics", "Mobofirm"),
        seo_title="Firmware categories explained",
        seo_description=(
            "Understand Official, Engineering, Readback, Modified, Other and "
            "Unclassified firmware categories and how package format differs."
        ),
        body="""
<p>Firmware is not all alike. Mobofirm groups every listing by <strong>where it came from</strong>, so you can decide how much risk you are willing to take.</p>
<h2>The categories</h2>
<ul>
<li><strong>Official.</strong> Published by the device manufacturer or an authorised channel. This is the right choice when you only want the stock software for your phone.</li>
<li><strong>Engineering.</strong> Builds used inside the manufacturer or its repair network, often with extra debug features. They may be unfinished and are not meant for everyday use.</li>
<li><strong>Readback.</strong> An image read from a working device. It can be a useful recovery source, but it may contain data tied to the original device.</li>
<li><strong>Modified.</strong> Official software that someone has altered, for example region conversions or pre-rooted images. The modification is the risk, so read the notes carefully.</li>
<li><strong>Other</strong> and <strong>Unclassified.</strong> Files that do not fit the groups above, or whose origin could not be established by a reviewer.</li>
</ul>
<h2>Category is not package format</h2>
<p>The category describes provenance. The <strong>package format</strong> describes how the files are organised, such as a recovery ROM, a fastboot ROM, a full OTA, a factory image or a service package. A listing can be official and still be shipped in any of those formats, and the file extension alone proves neither.</p>
<h2>Review comes first</h2>
<p>Staff with the right permission review every listing before it appears publicly, and record who approved it and when. Even so, always verify the model code and the published checksum before you flash.</p>
""",
    ),
    ArticleSeed(
        slug="back-up-your-android-phone-before-flashing",
        title="Back up your Android phone before flashing",
        summary=(
            "Flashing usually erases the phone. A short checklist protects your "
            "photos, messages, authenticator apps and accounts."
        ),
        category="getting-started",
        tags=("Backup", "Flashing", "Android"),
        seo_title="Back up your Android phone before flashing",
        seo_description=(
            "A practical checklist for backing up photos, messages, two-factor "
            "apps and accounts before you flash firmware or unlock a bootloader."
        ),
        body="""
<p>Most flashing and unlocking procedures wipe user data, and a failed attempt can force a reset. Spend ten minutes on a backup and you remove the worst part of the risk.</p>
<h2>What to back up</h2>
<ul>
<li><strong>Photos and videos.</strong> Copy them to a computer or confirm that your cloud gallery has finished syncing. Open the cloud app and check the item count rather than assuming.</li>
<li><strong>Contacts, calendar and messages.</strong> Use the built-in Google backup, or the manufacturer's own app. Samsung Smart Switch is an example for Galaxy devices.</li>
<li><strong>Two-factor authentication.</strong> Authenticator apps often do not move to a reset phone. Transfer each account to the new setup, or keep the recovery codes before you wipe anything.</li>
<li><strong>App data you care about.</strong> Messaging apps have their own backup option. Run it and confirm it finished.</li>
<li><strong>Passwords and passkeys.</strong> Confirm you can sign in to your password manager from another device.</li>
</ul>
<h2>Know your accounts</h2>
<p>After a reset, Factory Reset Protection asks for the Google account that was on the phone. Make sure you remember that account's password. If the phone uses an eSIM, ask your carrier how to re-issue it, because a reset can remove the profile.</p>
<h2>Final checks</h2>
<ol>
<li>Charge the phone above 50 percent.</li>
<li>Remove the SD card and SIM if the instructions allow it.</li>
<li>Write down the model code and the current build number.</li>
</ol>
""",
    ),
    ArticleSeed(
        slug="fastboot-download-mode-edl-and-brom-explained",
        title="Fastboot, Download mode, EDL and BROM: the phone's service modes",
        summary=(
            "Android phones expose several low-level modes for servicing. Learn "
            "what each one is for and which chipset families use it."
        ),
        category="hardware-explained",
        tags=("Fastboot", "EDL", "Chipsets"),
        seo_title="Fastboot, Download mode, EDL and BROM explained",
        seo_description=(
            "What Recovery, Fastboot, Samsung Download mode, Qualcomm EDL and "
            "MediaTek BROM are, and when each one is appropriate."
        ),
        body="""
<p>When Android does not start, or when you want to change what is installed, the phone offers lower-level modes. They differ by manufacturer and chipset, and the button combination to enter them differs even more, so always look up the exact instructions for your model.</p>
<h2>Recovery</h2>
<p>A small recovery system used for factory resets and for applying signed update packages. It is the safest mode and the one to try first.</p>
<h2>Fastboot (bootloader mode)</h2>
<p>A protocol served by the bootloader. With the Android SDK Platform-Tools you can read variables with <code>fastboot getvar</code> and, on an unlocked device, flash partition images. Most Google, OnePlus, Motorola, Nothing and Xiaomi workflows start here.</p>
<h2>Samsung Download mode</h2>
<p>Samsung's own mode, used by Odin and Heimdall to write the AP, BL, CP and CSC files.</p>
<h2>Qualcomm EDL (Emergency Download)</h2>
<p>A deep recovery mode in the chipset's boot ROM, shown on a computer as <em>Qualcomm HS-USB QDLoader 9008</em>. It needs a signed programmer made for the device, and many manufacturers restrict it to authorised service channels.</p>
<h2>MediaTek BROM and Preloader</h2>
<p>MediaTek chipsets expose a BootROM and Preloader USB mode that tools such as SP Flash Tool use together with a scatter file that describes the partition layout.</p>
<h2>Rule of thumb</h2>
<p>Use the highest-level mode that can solve the problem. Move from Recovery to Fastboot or Download mode first, and use EDL or BROM only with files that were built for your exact model.</p>
""",
    ),
    ArticleSeed(
        slug="samsung-firmware-version-codes-explained",
        title="Reading a Samsung firmware version code",
        summary=(
            "Strings such as S928BXXU1AXA7 look random, but they follow a pattern "
            "that tells you the model, region, revision and build month."
        ),
        category="hardware-explained",
        tags=("Samsung", "Build numbers", "Firmware basics"),
        seo_title="Reading a Samsung firmware version code",
        seo_description=(
            "Decode a Samsung PDA firmware string such as S928BXXU1AXA7 into model "
            "code, region, bootloader revision, build month and iteration."
        ),
        body="""
<p>Samsung identifies a firmware release with a PDA version string. Using <code>S928BXXU1AXA7</code> as an example, the parts line up like this. The exact meaning of the early letters can vary slightly between device generations, so treat the breakdown as a reading guide.</p>
<table>
<thead><tr><th scope="col">Part</th><th scope="col">Example</th><th scope="col">Meaning</th></tr></thead>
<tbody>
<tr><td>Model</td><td><code>S928B</code></td><td>The model code without the <code>SM-</code> prefix</td></tr>
<tr><td>Region group</td><td><code>XX</code></td><td>The sales region the build is meant for</td></tr>
<tr><td>Build type</td><td><code>U</code></td><td>A user (retail) build</td></tr>
<tr><td>Bootloader level</td><td><code>1</code></td><td>The bootloader or binary version; it only goes up</td></tr>
<tr><td>Revision</td><td><code>A</code></td><td>The update revision, advancing through the alphabet</td></tr>
<tr><td>Year</td><td><code>X</code></td><td>The release year, where A is 2001, so X is 2024</td></tr>
<tr><td>Month</td><td><code>A</code></td><td>The release month, where A is January and L is December</td></tr>
<tr><td>Iteration</td><td><code>7</code></td><td>The build number within that month</td></tr>
</tbody>
</table>
<h2>Why the bootloader digit matters</h2>
<p>Samsung devices refuse to go back to a lower bootloader level once a higher one has been installed. If a package has a lower digit than the phone, flashing it can fail or stop the phone from booting. See our article on anti-rollback protection for the broader picture.</p>
<h2>CSC and region</h2>
<p>The CSC file selects the region and carrier customisation. A package built for one CSC may change language, features and network settings on another region's phone, so pick the CSC of the phone's original market unless you know why you are changing it.</p>
""",
    ),
    ArticleSeed(
        slug="android-security-patch-levels-explained",
        title="Android security patch levels explained",
        summary=(
            "The security patch date shows which known vulnerabilities your build "
            "includes. Learn how to read it and why it matters for firmware choices."
        ),
        category="security-and-safety",
        tags=("Security patch", "Security", "Android"),
        seo_title="Android security patch levels explained",
        seo_description=(
            "What the Android security patch level means, where to find it and why "
            "you should prefer firmware with a recent patch date."
        ),
        body="""
<p>Android publishes security fixes every month. A phone's <strong>security patch level</strong> is a date such as <code>2024-03-05</code> that states which of those monthly bulletins the build includes. A newer date means more known vulnerabilities are fixed.</p>
<h2>Where to read it</h2>
<ul>
<li>Settings, then About phone, then Android version: look for Android security update.</li>
<li>With ADB: <code>adb shell getprop ro.build.version.security_patch</code>.</li>
</ul>
<h2>How to use the date when choosing firmware</h2>
<ol>
<li><strong>Prefer the newest build for your exact model code.</strong> The patch level of a package is listed with its build details in the catalog.</li>
<li><strong>Do not go backwards without a reason.</strong> Older builds lack fixes and can trigger rollback protection on newer devices.</li>
<li><strong>Remember that the date is a statement, not a guarantee.</strong> Manufacturers sometimes skip individual patches, and the patch level does not cover every layer of the software.</li>
</ol>
<h2>When the date is old</h2>
<p>If your device no longer receives updates, the patch date will not move. Check the manufacturer's support policy, and avoid using such a phone for banking and other sensitive tasks where you can.</p>
""",
    ),
    ArticleSeed(
        slug="install-adb-and-fastboot-on-windows-macos-and-linux",
        title="Install ADB and Fastboot on Windows, macOS and Linux",
        summary=(
            "Set up the Android SDK Platform-Tools, authorise your phone and "
            "confirm the connection before you run any flashing command."
        ),
        category="flashing-and-tools",
        tags=("ADB", "Fastboot", "Platform-Tools"),
        seo_title="Install ADB and Fastboot on any computer",
        seo_description=(
            "Download the Android SDK Platform-Tools, enable USB debugging and "
            "verify ADB and Fastboot detect your phone on Windows, macOS or Linux."
        ),
        body="""
<p>ADB talks to a running phone and Fastboot talks to a phone in bootloader mode. Both ship in Google's <a href="https://developer.android.com/tools/releases/platform-tools">Android SDK Platform-Tools</a>, a small archive that needs no installer.</p>
<h2>Set up</h2>
<ol>
<li>Download the Platform-Tools archive for your operating system from Google and extract it to a folder you can find again.</li>
<li>Open a terminal in that folder. On Windows you can use PowerShell, and on macOS or Linux any terminal.</li>
<li>On the phone, tap Build number seven times in About phone, then open Developer options and enable <strong>USB debugging</strong>.</li>
<li>Connect the phone with a data-capable cable and accept the RSA fingerprint prompt on the screen.</li>
<li>Run <code>adb devices</code>. The phone should appear with the state <code>device</code>. If it says <code>unauthorized</code>, accept the prompt on the phone.</li>
</ol>
<h2>Check Fastboot</h2>
<p>Run <code>adb reboot bootloader</code> and then <code>fastboot devices</code>. If the phone is listed, Fastboot works. Return to Android with <code>fastboot reboot</code>.</p>
<h2>If the phone is not found</h2>
<ul>
<li><strong>Windows:</strong> install the manufacturer's USB driver or Google's USB driver and check Device Manager for warning icons.</li>
<li><strong>Linux:</strong> add a udev rule for your vendor ID, or run the tools through a packaged <code>android-tools</code> install that includes the rules.</li>
<li><strong>All systems:</strong> try another cable and a port on the computer itself rather than a hub.</li>
</ul>
""",
    ),
    ArticleSeed(
        slug="a-b-seamless-updates-and-virtual-a-b-explained",
        title="A/B seamless updates and what they mean for flashing",
        summary=(
            "Modern Android phones keep two copies of the system. Learn how that "
            "changes updates, rollbacks and the way you flash images."
        ),
        category="hardware-explained",
        tags=("A/B updates", "Fastboot", "Android"),
        seo_title="A/B seamless updates explained for flashing",
        seo_description=(
            "How Android A/B and Virtual A/B slots work, how to read the active "
            "slot with Fastboot and what it means when you flash images."
        ),
        body="""
<p>Many phones released since Android 8 use <strong>A/B seamless updates</strong>. The system exists in two slots, <code>_a</code> and <code>_b</code>. An update is written to the inactive slot while you keep using the phone, and the device switches slots at the next restart. If the new slot fails to boot, the phone can fall back to the old one.</p>
<h2>Virtual A/B</h2>
<p>Newer devices use Virtual A/B, which saves storage by keeping a snapshot of changed blocks instead of a full second copy. From the outside it behaves the same way, and the update is merged after a successful boot.</p>
<h2>What to know when flashing</h2>
<ul>
<li>Read the active slot with <code>fastboot getvar current-slot</code>.</li>
<li>A manufacturer's flash script usually writes to a specific slot, or to both. Follow the script rather than flashing partitions one by one unless you understand the layout.</li>
<li>Flashing only one slot can leave the other slot on older software. That slot is what the phone may fall back to, so a mismatch can cause surprising behaviour.</li>
<li>Some partitions, such as the bootloader, are protected by rollback rules. Read our article on anti-rollback protection before writing them.</li>
</ul>
<h2>Why it matters</h2>
<p>A/B makes official over-the-air updates much safer. It does not make manual flashing risk free, because a wrong image can still affect both slots.</p>
""",
    ),
    ArticleSeed(
        slug="anti-rollback-protection-why-downgrades-can-brick",
        title="Anti-rollback protection: why downgrading can brick a phone",
        summary=(
            "Manufacturers record a minimum software version in hardware. Flashing "
            "older firmware than that limit can stop the phone from starting."
        ),
        category="security-and-safety",
        tags=("Anti-rollback", "Security", "Flashing"),
        seo_title="Anti-rollback protection and firmware downgrades",
        seo_description=(
            "Why modern phones refuse older firmware, how rollback indexes work "
            "and the checks to make before you flash a downgrade."
        ),
        body="""
<p>Security fixes would be pointless if anyone could install the old, vulnerable software again. To prevent that, many phones store a <strong>rollback index</strong> in tamper-resistant memory. Each update can raise the number. The bootloader then refuses any image whose index is lower than the stored value.</p>
<h2>How it shows up</h2>
<ul>
<li>A flash tool reports an error such as a failed signature or version check.</li>
<li>Or, worse, the flash succeeds and the phone does not boot, because the bootloader rejects the system it was given.</li>
</ul>
<h2>Check before you downgrade</h2>
<ol>
<li>Find the current build and security patch date of the phone.</li>
<li>Find the release notes or build details of the package you want to flash. A package that is older than the installed build is a downgrade.</li>
<li>Look for the manufacturer's rollback or anti-rollback value in its documentation. Some devices expose a variable in Fastboot, but the name differs by brand.</li>
<li>If you cannot confirm that the package is allowed, do not flash it.</li>
</ol>
<h2>Safer alternatives</h2>
<p>If the latest software causes a problem, check whether the manufacturer offers a supported rollback, report the issue to them or wait for the next update. Reinstalling the same build you already run, or a newer one, avoids the whole class of rollback problems.</p>
""",
    ),
    ArticleSeed(
        slug="usb-cable-and-driver-troubleshooting-for-flashing",
        title="USB cable and driver troubleshooting for flashing",
        summary=(
            "Most failed flashes are connection problems. Work through cables, "
            "ports and drivers before you suspect the firmware."
        ),
        category="flashing-and-tools",
        tags=("Troubleshooting", "USB drivers", "Flashing"),
        seo_title="USB cable and driver troubleshooting for flashing",
        seo_description=(
            "Fix 'device not found' and mid-flash disconnects by checking the "
            "cable, the port and the USB drivers in a logical order."
        ),
        body="""
<p>A flash tool that cannot see the phone, or loses it halfway through, is almost always reporting a connection problem. Work through the list in order.</p>
<h2>1. Use a good cable</h2>
<p>Charge-only cables lack data wires, and worn cables drop out under load. Use the cable that came with the phone, or a short, well-made data cable.</p>
<h2>2. Choose the right port</h2>
<ul>
<li>Plug into a port on the computer itself, preferably at the back of a desktop.</li>
<li>Avoid hubs, docks and extension leads.</li>
<li>If a USB 3 port misbehaves, try a USB 2 port.</li>
</ul>
<h2>3. Install the right driver</h2>
<ul>
<li><strong>Android devices in general:</strong> the manufacturer's USB driver, or Google's USB driver.</li>
<li><strong>Samsung Download mode:</strong> Samsung's USB driver.</li>
<li><strong>Qualcomm EDL:</strong> the Qualcomm HS-USB QDLoader 9008 driver.</li>
<li><strong>MediaTek BROM:</strong> the MediaTek USB VCOM driver.</li>
</ul>
<p>Open Device Manager with the phone connected in the right mode. A device with a yellow warning icon means the driver is missing or mismatched.</p>
<h2>4. Remove conflicts</h2>
<p>Close phone-manager programs that also talk to the device, restart the computer and try again from a clean boot.</p>
<h2>5. Still stuck?</h2>
<p>Try a second computer. If a second machine behaves the same way, the cable, the phone's port or the firmware mode is the likelier cause.</p>
""",
    ),
    ArticleSeed(
        slug="mobile-chipset-families-and-what-they-mean-for-flashing",
        title="Mobile chipset families and what they mean for flashing",
        summary=(
            "Snapdragon, Dimensity, Exynos, Tensor, Unisoc and Kirin each use "
            "different service modes and tools. Know your chipset before you flash."
        ),
        category="hardware-explained",
        tags=("Chipsets", "Snapdragon", "MediaTek"),
        seo_title="Chipset families and what they mean for flashing",
        seo_description=(
            "How Qualcomm Snapdragon, MediaTek Dimensity and Helio, Samsung "
            "Exynos, Google Tensor, Unisoc and Kirin differ for firmware flashing."
        ),
        body="""
<p>The system-on-chip inside a phone decides which low-level service mode exists and which tools can use it. The catalog lists the chipset of every device so you can see this at a glance.</p>
<table>
<thead><tr><th scope="col">Family</th><th scope="col">Maker</th><th scope="col">Typical flashing route</th></tr></thead>
<tbody>
<tr><td>Snapdragon</td><td>Qualcomm</td><td>Fastboot for normal work; EDL (9008) with a signed programmer for deep recovery</td></tr>
<tr><td>Dimensity and Helio</td><td>MediaTek</td><td>Fastboot where the maker allows it; BROM and Preloader with a scatter file and SP Flash Tool</td></tr>
<tr><td>Exynos</td><td>Samsung</td><td>Download mode with Odin or Heimdall</td></tr>
<tr><td>Tensor</td><td>Google</td><td>Fastboot and Google's factory images, or the Android Flash Tool</td></tr>
<tr><td>Unisoc</td><td>Unisoc</td><td>Unisoc download mode with <code>.pac</code> packages</td></tr>
<tr><td>Kirin</td><td>HiSilicon (Huawei)</td><td>The manufacturer's recovery and update tools</td></tr>
</tbody>
</table>
<h2>The same phone, different chips</h2>
<p>Some models use different chipsets in different regions. Samsung's flagship phones are a well-known example. A package for one variant will not work on another, which is why each listing in the catalog is tied to a variant and its model code.</p>
<h2>Using the chipset pages</h2>
<p>Each chipset in the catalog has its own page with the devices that use it, the firmware available for them and the tools that support the family. Start there when you know the chipset but are unsure about the process.</p>
""",
    ),
    ArticleSeed(
        slug="how-mobofirm-lists-external-firmware-downloads",
        title="How Mobofirm lists externally hosted firmware downloads",
        summary=(
            "Many firmware files are hosted by the manufacturer or another trusted "
            "site. This is how external listings are submitted, reviewed and labelled."
        ),
        category="about-the-catalog",
        tags=("Mobofirm", "Firmware categories", "Security"),
        seo_title="How Mobofirm lists external firmware downloads",
        seo_description=(
            "How externally hosted firmware is submitted, reviewed and labelled "
            "on Mobofirm, and what to check before you follow a link."
        ),
        body="""
<p>Not every firmware file lives on our servers. A listing can point to a file hosted elsewhere, for example by the manufacturer. Mobofirm shows these as <strong>External download</strong> everywhere they appear, so you always know where you are being sent.</p>
<h2>How a listing gets here</h2>
<ol>
<li>A contributor submits the download link together with the brand, model and variant, and the package format.</li>
<li>The link must use HTTPS and point to a host that site staff have placed on an allow-list. Links to other hosts are rejected.</li>
<li>A staff reviewer with the right permission approves or rejects the submission, chooses the firmware category and records the decision and the time.</li>
<li>Only after approval does the listing appear in the catalog, on the model page and in the automatically maintained model, brand and chipset articles.</li>
</ol>
<h2>What you see</h2>
<ul>
<li>An External download badge on the listing and on the download page.</li>
<li>The host that will receive you, shown before you continue.</li>
<li>The build number, Android version and package format, when the reviewer recorded them.</li>
</ul>
<h2>What you should still do</h2>
<ul>
<li>Match the model code and variant first.</li>
<li>Compare the checksum with a value published by the manufacturer.</li>
<li>Prefer official listings if you only need stock software.</li>
</ul>
<p>We cannot control the content of external hosts, which is why the review step and your own checks both matter.</p>
""",
    ),
)
