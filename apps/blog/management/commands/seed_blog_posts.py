"""
Management command to create dummy blog posts WITHOUT tags.
This is for testing auto-tagging functionality.
"""

import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

User = get_user_model()


# Blog post topics related to mobile firmware and technology
BLOG_TOPICS = [
    {
        "title": "Complete Guide to Android Flash Tools",
        "summary": "Learn how to use various flash tools for Android devices including SP Flash Tool, Odin, and QFIL. This comprehensive guide covers all the essential steps.",
        "body": """
<h2>Introduction to Android Flash Tools</h2>
<p>Flashing firmware on Android devices requires specialized tools depending on the chipset manufacturer. This guide covers the most popular flash tools used by technicians worldwide.</p>

<h3>SP Flash Tool for MediaTek Devices</h3>
<p>SP Flash Tool (Smart Phone Flash Tool) is the official flashing software for MediaTek (MTK) chipset devices. It's widely used for flashing stock ROMs, custom recoveries, and performing unbrick operations.</p>
<p>Key features include scatter file support, download agent compatibility, and memory test functions. The tool works with Windows operating systems and requires proper USB drivers.</p>

<h3>Odin for Samsung Devices</h3>
<p>Odin is Samsung's proprietary flashing tool used for flashing firmware, recovery images, and kernels on Samsung Galaxy devices. It recognizes devices in download mode and processes .tar and .md5 firmware files.</p>

<h3>QFIL for Qualcomm Devices</h3>
<p>Qualcomm Flash Image Loader (QFIL) is used for Qualcomm Snapdragon chipset devices. It's essential for flashing factory firmware and unbricking devices stuck in EDL mode.</p>

<h2>Safety Precautions</h2>
<ul>
<li>Always backup your data before flashing</li>
<li>Ensure battery is at least 50% charged</li>
<li>Use original USB cables</li>
<li>Download firmware from trusted sources</li>
</ul>

<p>Following these guidelines will help ensure successful firmware installation and device recovery operations.</p>
""",
    },
    {
        "title": "Understanding Mobile Chipset Architectures",
        "summary": "A deep dive into Qualcomm Snapdragon, MediaTek Helio, and Samsung Exynos chipset architectures and their implications for firmware development.",
        "body": """
<h2>Mobile Chipset Overview</h2>
<p>Mobile processors have evolved significantly over the years. Understanding chipset architecture is crucial for firmware developers and mobile technicians.</p>

<h3>Qualcomm Snapdragon Series</h3>
<p>Qualcomm's Snapdragon processors dominate the premium smartphone market. The Snapdragon 8 Gen 3 represents the latest in mobile processing power with advanced AI capabilities.</p>
<p>Key technologies include Adreno GPU, Hexagon DSP, and integrated 5G modems. Firmware for Snapdragon devices typically uses QFIL and requires specific firehose programmers.</p>

<h3>MediaTek Helio and Dimensity</h3>
<p>MediaTek has made significant strides with their Helio and Dimensity series. These chipsets offer excellent value and strong performance in mid-range devices.</p>
<p>The MT6xxx series (like MT6765, MT6833) are common in budget smartphones. Firmware development uses scatter files and download agents specific to each chipset variant.</p>

<h3>Samsung Exynos</h3>
<p>Samsung's Exynos processors power many Galaxy devices in certain regions. These ARM-based chips feature custom GPU designs and integrated modems.</p>

<h2>Firmware Considerations</h2>
<p>Each chipset family has unique firmware requirements:</p>
<ul>
<li>Boot loaders and partition schemes differ</li>
<li>Security mechanisms vary (Knox, vbmeta)</li>
<li>Driver compatibility is chipset-specific</li>
</ul>
""",
    },
    {
        "title": "How to Unbrick Your Android Device",
        "summary": "Step-by-step guide to recover bricked Android phones and tablets. Covers soft brick, hard brick, and bootloop recovery methods.",
        "body": """
<h2>Understanding Brick States</h2>
<p>A "bricked" device is one that won't boot normally. Understanding the type of brick is essential for choosing the right recovery method.</p>

<h3>Soft Brick</h3>
<p>A soft brick occurs when the device boots but gets stuck in a boot loop or can't complete the startup process. This is usually recoverable through recovery mode or fastboot.</p>
<p>Common causes include failed OTA updates, corrupt system files, or incompatible modifications.</p>

<h3>Hard Brick</h3>
<p>A hard brick means the device shows no signs of life - no screen, no LED, no response. This usually indicates corrupted bootloader or critical partition damage.</p>
<p>Recovery typically requires specialized tools like EDL mode for Qualcomm or Download mode for Samsung devices.</p>

<h2>Recovery Methods</h2>

<h3>Method 1: Recovery Mode Flash</h3>
<p>Boot into recovery mode (usually Volume Up + Power) and perform a factory reset or flash a new ROM using ADB sideload.</p>

<h3>Method 2: Fastboot Commands</h3>
<p>Use fastboot mode to flash individual partitions:</p>
<code>fastboot flash boot boot.img</code>
<code>fastboot flash system system.img</code>

<h3>Method 3: EDL Mode Recovery</h3>
<p>For severely bricked Qualcomm devices, EDL (Emergency Download) mode allows low-level firmware restoration using QFIL tool.</p>

<h2>Prevention Tips</h2>
<ul>
<li>Always create full backups before modifications</li>
<li>Verify firmware compatibility with your device model</li>
<li>Never interrupt the flashing process</li>
<li>Keep stock firmware available for emergency recovery</li>
</ul>
""",
    },
    {
        "title": "GSM Unlocking Methods Explained",
        "summary": "Understanding different methods to unlock GSM phones including official unlocks, IMEI unlocking, and software solutions.",
        "body": """
<h2>What is GSM Unlocking?</h2>
<p>GSM unlocking removes carrier restrictions from mobile devices, allowing them to work with any compatible SIM card worldwide.</p>

<h3>Official Carrier Unlock</h3>
<p>The safest method is requesting an unlock from your carrier. Most carriers provide free unlocking after contract completion or paid device ownership verification.</p>
<p>This method permanently unlocks the device and is fully legal in most countries.</p>

<h3>IMEI-Based Unlocking</h3>
<p>Third-party services can unlock devices using the IMEI number. They submit unlock requests to carrier databases or use alternative methods to whitelist your device.</p>
<p>Processing times vary from hours to days depending on the carrier and device model.</p>

<h3>Software Unlocking</h3>
<p>Some devices can be unlocked using specialized software tools. This method is model-specific and may require technical expertise.</p>
<p>Tools like DC-Unlocker, Octoplus, and Z3X provide unlocking capabilities for various brands.</p>

<h2>Important Considerations</h2>
<ul>
<li>Check warranty implications before unlocking</li>
<li>Verify the device is eligible for unlocking</li>
<li>Beware of scam services offering instant unlocks</li>
<li>Keep proof of purchase for verification</li>
</ul>

<h2>Legal Status</h2>
<p>Phone unlocking is legal in most countries including the USA (since 2014), UK, and EU member states. However, unlocking stolen devices is illegal everywhere.</p>
""",
    },
    {
        "title": "Custom ROM Installation Guide for Beginners",
        "summary": "A beginner-friendly guide to installing custom Android ROMs. Covers bootloader unlocking, custom recovery, and ROM flashing.",
        "body": """
<h2>What are Custom ROMs?</h2>
<p>Custom ROMs are modified versions of Android operating system created by third-party developers. They offer features, performance improvements, and updates not available in stock firmware.</p>

<h3>Popular Custom ROMs</h3>
<p>LineageOS, Pixel Experience, and crDroid are among the most popular custom ROMs. Each offers different features and focuses on various aspects of the Android experience.</p>

<h2>Prerequisites</h2>
<ol>
<li>Unlocked bootloader</li>
<li>Custom recovery (TWRP recommended)</li>
<li>Compatible ROM for your device</li>
<li>GApps package (if needed)</li>
<li>Full device backup</li>
</ol>

<h3>Unlocking the Bootloader</h3>
<p>Most manufacturers require you to enable OEM unlocking in developer options and use fastboot commands to unlock the bootloader.</p>
<p><strong>Warning:</strong> Unlocking bootloader typically wipes all data and may void warranty.</p>

<h3>Installing Custom Recovery</h3>
<p>TWRP (Team Win Recovery Project) is the most popular custom recovery. Flash it using fastboot:</p>
<code>fastboot flash recovery twrp-recovery.img</code>

<h2>ROM Installation Steps</h2>
<ol>
<li>Boot into TWRP recovery</li>
<li>Wipe data, cache, and dalvik</li>
<li>Flash the ROM zip file</li>
<li>Flash GApps (Google Apps) if desired</li>
<li>Reboot and enjoy your new ROM</li>
</ol>

<p>First boot may take 5-10 minutes. Be patient and don't interrupt the process.</p>
""",
    },
    {
        "title": "Mobile Device Security: Firmware Perspective",
        "summary": "Understanding mobile security from a firmware standpoint including secure boot, encryption, and trusted execution environments.",
        "body": """
<h2>Firmware Security Fundamentals</h2>
<p>Modern smartphones implement multiple layers of security at the firmware level. Understanding these mechanisms is crucial for both developers and security researchers.</p>

<h3>Secure Boot Chain</h3>
<p>The secure boot process ensures only authenticated code runs on the device:</p>
<ol>
<li>ROM bootloader verifies primary bootloader</li>
<li>Primary bootloader verifies secondary bootloader</li>
<li>Secondary bootloader verifies kernel and system</li>
</ol>
<p>Any verification failure prevents boot, protecting against malicious modifications.</p>

<h3>Trusted Execution Environment (TEE)</h3>
<p>ARM TrustZone creates an isolated secure world for sensitive operations like biometric processing and DRM. Qualcomm's QSEE and Samsung's Knox are TEE implementations.</p>

<h3>Full Disk Encryption</h3>
<p>Android devices use file-based encryption (FBE) or full-disk encryption (FDE) to protect user data. Encryption keys are derived from user credentials and hardware-bound secrets.</p>

<h2>Security Implications for Firmware Work</h2>
<ul>
<li>Bootloader unlocking disables some security features</li>
<li>Custom ROMs may lack security certifications</li>
<li>Downgrade attacks may be blocked by anti-rollback</li>
<li>Factory resets may not clear all secure data</li>
</ul>

<h2>Best Practices</h2>
<p>For maximum security, keep devices on official firmware with latest patches. Only modify devices when you understand and accept the security trade-offs.</p>
""",
    },
    {
        "title": "TWRP Recovery: Advanced Features Guide",
        "summary": "Master TWRP recovery with this comprehensive guide covering backups, restores, partition management, and advanced operations.",
        "body": """
<h2>TWRP Overview</h2>
<p>Team Win Recovery Project (TWRP) is the most versatile custom recovery available for Android devices. It provides a touchscreen interface for device maintenance operations.</p>

<h3>Creating Full Backups</h3>
<p>TWRP's backup feature creates complete system images including boot, system, data, and other partitions. These NANDroid backups can restore your device to any previous state.</p>
<p>Recommended backup selections: Boot, System, Data, EFS (contains IMEI), Vendor.</p>

<h3>Restoring Backups</h3>
<p>To restore a backup:</p>
<ol>
<li>Navigate to Restore menu</li>
<li>Select backup from list</li>
<li>Choose partitions to restore</li>
<li>Swipe to confirm</li>
</ol>

<h2>Advanced Operations</h2>

<h3>Partition Management</h3>
<p>TWRP can resize partitions, change file systems, and manage storage. Use with caution as improper partition changes can brick devices.</p>

<h3>ADB Sideload</h3>
<p>Sideload feature allows flashing files from computer via ADB without copying to device storage. Useful when internal storage is inaccessible.</p>

<h3>Terminal Access</h3>
<p>Built-in terminal provides shell access for advanced users. Useful for partition manipulation, file operations, and troubleshooting.</p>

<h2>TWRP Tips</h2>
<ul>
<li>Keep TWRP updated for latest device support</li>
<li>Store backups on external SD or PC</li>
<li>Verify backup integrity after creation</li>
<li>Learn partition layout of your device</li>
</ul>
""",
    },
    {
        "title": "Firmware Download Safety: Avoiding Malware",
        "summary": "How to safely download firmware files and avoid malware-infected ROMs. Essential security tips for mobile technicians.",
        "body": """
<h2>The Risk of Malware in Firmware</h2>
<p>Downloading firmware from untrusted sources poses significant security risks. Malware can be embedded in modified firmware files, compromising devices and user data.</p>

<h3>Common Malware Types</h3>
<ul>
<li><strong>Trojans:</strong> Hidden in seemingly legitimate firmware</li>
<li><strong>Spyware:</strong> Monitors user activity and steals data</li>
<li><strong>Adware:</strong> Pre-installed apps that display unwanted ads</li>
<li><strong>Ransomware:</strong> Encrypts data and demands payment</li>
</ul>

<h2>Safe Download Practices</h2>

<h3>Trusted Sources</h3>
<p>Always download firmware from official manufacturer websites or reputable community sources:</p>
<ul>
<li>Samsung: samsung.com/firmware</li>
<li>Xiaomi: Official MIUI forums</li>
<li>OnePlus: oneplus.com/support</li>
<li>XDA Developers for community ROMs</li>
</ul>

<h3>Verification Methods</h3>
<p>Verify firmware integrity using checksums:</p>
<ul>
<li>Compare MD5/SHA1 hashes with official values</li>
<li>Check digital signatures when available</li>
<li>Verify file sizes match expected values</li>
</ul>

<h3>Scanning Files</h3>
<p>Before flashing, scan firmware files with updated antivirus software. Online scanners like VirusTotal can check files against multiple engines.</p>

<h2>Red Flags to Watch</h2>
<ul>
<li>Firmware only available from one unknown source</li>
<li>Files significantly smaller/larger than expected</li>
<li>Missing or mismatched checksums</li>
<li>Requests to disable security software</li>
</ul>
""",
    },
    {
        "title": "Understanding Android Partition Layout",
        "summary": "Comprehensive guide to Android partition structure including boot, system, vendor, userdata, and other critical partitions.",
        "body": """
<h2>Android Partition Basics</h2>
<p>Android devices use multiple partitions to organize system components and user data. Understanding this layout is essential for firmware work.</p>

<h3>Critical Partitions</h3>

<h4>Boot Partition</h4>
<p>Contains the kernel and ramdisk. Essential for device startup. Corruption here causes boot failures.</p>

<h4>System Partition</h4>
<p>Holds the Android operating system, framework, and pre-installed apps. On A/B devices, there are system_a and system_b partitions.</p>

<h4>Vendor Partition</h4>
<p>Contains device-specific HAL implementations and drivers. Introduced with Project Treble for better modularity.</p>

<h4>Userdata Partition</h4>
<p>Stores user-installed apps, settings, and personal data. Encrypted on modern devices.</p>

<h4>Recovery Partition</h4>
<p>Contains the recovery system for maintenance operations. Custom recoveries like TWRP replace this partition.</p>

<h3>Other Important Partitions</h3>
<ul>
<li><strong>EFS:</strong> Stores IMEI, MAC addresses, and calibration data</li>
<li><strong>Modem:</strong> Baseband firmware for cellular connectivity</li>
<li><strong>Cache:</strong> Temporary files and OTA updates</li>
<li><strong>Persist:</strong> Sensor calibration and factory data</li>
</ul>

<h2>A/B Partition Scheme</h2>
<p>Modern devices use A/B (seamless) updates with duplicate system partitions. This allows updates while the device is in use and provides rollback capability.</p>

<h2>Super Partition</h2>
<p>Android 10+ introduces dynamic partitions within a super partition. System, vendor, and product partitions are logical volumes that can be resized.</p>
""",
    },
    {
        "title": "FRP Bypass: What You Need to Know",
        "summary": "Understanding Factory Reset Protection (FRP) and legitimate methods to bypass it when you're locked out of your own device.",
        "body": """
<h2>What is FRP?</h2>
<p>Factory Reset Protection (FRP) is a security feature introduced in Android 5.1 Lollipop. It prevents unauthorized use of a device after a factory reset by requiring the previous Google account credentials.</p>

<h3>How FRP Works</h3>
<ol>
<li>Google account added to device</li>
<li>FRP flag set in secure partition</li>
<li>After factory reset, device requires same account</li>
<li>Verification needed before device setup completes</li>
</ol>

<h2>Legitimate Bypass Scenarios</h2>
<p>There are valid reasons you might need to bypass FRP:</p>
<ul>
<li>Forgotten Google account credentials</li>
<li>Purchased used device with previous account</li>
<li>Company device with departed employee's account</li>
<li>Device received as gift without account removal</li>
</ul>

<h3>Official Methods</h3>
<ol>
<li><strong>Google Account Recovery:</strong> Reset password through Google's recovery process</li>
<li><strong>Previous Owner:</strong> Contact seller to remove account remotely</li>
<li><strong>Manufacturer Service:</strong> Some brands offer FRP removal with proof of purchase</li>
<li><strong>Carrier Support:</strong> Original carrier may assist with account verification</li>
</ol>

<h2>Important Notes</h2>
<ul>
<li>Bypassing FRP on stolen devices is illegal</li>
<li>Many bypass methods are patched in security updates</li>
<li>Professional FRP removal services require proof of ownership</li>
<li>Device may need to be connected to internet for bypass verification</li>
</ul>

<p>Always attempt official recovery methods first. Keep proof of purchase for any device you own.</p>
""",
    },
    {
        "title": "Xiaomi Firmware: MIUI vs Stock Android",
        "summary": "Comparing MIUI and Stock Android on Xiaomi devices. Understanding the differences and how to switch between them.",
        "body": """
<h2>MIUI Overview</h2>
<p>MIUI is Xiaomi's custom Android skin known for its iOS-like appearance and extensive customization options. It includes unique features not found in stock Android.</p>

<h3>MIUI Features</h3>
<ul>
<li>Themes engine with deep customization</li>
<li>Second space for private apps</li>
<li>Dual apps for multiple accounts</li>
<li>Game Turbo optimization</li>
<li>MIUI optimization toggle</li>
</ul>

<h3>MIUI Drawbacks</h3>
<ul>
<li>Pre-installed apps (bloatware)</li>
<li>Occasional ads in system apps</li>
<li>Delayed Android updates</li>
<li>Heavy resource usage</li>
</ul>

<h2>Xiaomi.eu ROM</h2>
<p>Xiaomi.eu is a debloated, optimized version of official MIUI. It removes ads, adds Google services by default, and includes weekly updates from China ROM.</p>

<h2>Stock Android on Xiaomi</h2>
<p>Many Xiaomi devices support custom ROMs like LineageOS, Pixel Experience, and ArrowOS. These provide pure Android experience with:</p>
<ul>
<li>Faster performance</li>
<li>Better battery life</li>
<li>Quick security updates</li>
<li>No bloatware or ads</li>
</ul>

<h3>Switching to Stock Android</h3>
<ol>
<li>Unlock bootloader (wait period required)</li>
<li>Install custom recovery</li>
<li>Flash desired ROM</li>
<li>Optional: Flash GApps</li>
</ol>

<h2>Which Should You Choose?</h2>
<p>Choose MIUI if you want Xiaomi's unique features. Choose stock Android for cleaner experience and faster updates. Xiaomi.eu offers a middle ground.</p>
""",
    },
    {
        "title": "Samsung Knox: Security Features Explained",
        "summary": "Deep dive into Samsung Knox security platform covering Secure Folder, Knox Workspace, and implications for firmware modifications.",
        "body": """
<h2>What is Samsung Knox?</h2>
<p>Knox is Samsung's defense-grade mobile security platform. It provides hardware-backed security features for enterprise and consumer devices.</p>

<h3>Knox Architecture</h3>
<p>Knox security is built into the device from manufacturing:</p>
<ul>
<li>Hardware root of trust</li>
<li>Secure boot chain</li>
<li>Real-time kernel protection</li>
<li>TrustZone-based Integrity Measurement</li>
</ul>

<h2>Consumer Features</h2>

<h3>Secure Folder</h3>
<p>Encrypted container for sensitive apps and files. Uses separate encryption key and can have different lock method than main device.</p>

<h3>Private Share</h3>
<p>Share files with expiration dates and access controls. Recipients cannot screenshot or share further.</p>

<h2>Enterprise Features</h2>
<ul>
<li><strong>Knox Workspace:</strong> Separate work profile with IT control</li>
<li><strong>Knox Configure:</strong> Mass device deployment</li>
<li><strong>Knox Mobile Enrollment:</strong> Automatic MDM registration</li>
<li><strong>Knox Vault:</strong> Hardware security module</li>
</ul>

<h2>Knox and Firmware Modifications</h2>
<p>Knox includes a physical e-fuse that trips when:</p>
<ul>
<li>Bootloader is unlocked</li>
<li>Custom ROM is flashed</li>
<li>Root access is obtained</li>
</ul>

<p><strong>Consequences of tripped Knox:</strong></p>
<ul>
<li>Warranty void (in most regions)</li>
<li>Samsung Pay disabled</li>
<li>Secure Folder inaccessible</li>
<li>Some apps may not work</li>
</ul>

<p>Knox counter cannot be reset. Consider carefully before modifying Samsung devices.</p>
""",
    },
]


class Command(BaseCommand):
    help = "Create dummy blog posts WITHOUT tags for testing auto-tagging"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10,
            help="Number of blog posts to create (default: 10)",
        )

    def handle(self, *args, **options):
        from apps.blog.models import Category, Post, PostStatus

        count = min(options["count"], len(BLOG_TOPICS))

        # Get or create admin user
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.filter(is_staff=True).first()
        if not admin_user:
            admin_user = User.objects.first()

        if not admin_user:
            self.stdout.write(self.style.ERROR("No users found. Create a user first."))
            return

        # Get or create default category
        category, _ = Category.objects.get_or_create(
            name="Technology", defaults={"slug": "technology"}
        )

        created_count = 0

        for i, topic in enumerate(BLOG_TOPICS[:count]):
            title = topic["title"]
            slug = slugify(title)

            # Check if post already exists
            if Post.objects.filter(slug=slug).exists():
                self.stdout.write(f"  Skipping (exists): {title}")
                continue

            Post.objects.create(
                title=title,
                slug=slug,
                summary=topic["summary"],
                body=topic["body"],
                author=admin_user,
                category=category,
                status=PostStatus.PUBLISHED,
                is_published=True,
                published_at=timezone.now(),
                publish_at=timezone.now(),
                reading_time=random.randint(5, 15),  # noqa: S311
                allow_comments=True,
                featured=i < 3,  # First 3 are featured
            )

            # Explicitly DO NOT add any tags
            # This is intentional for testing auto-tagging

            created_count += 1
            self.stdout.write(self.style.SUCCESS(f"✓ Created: {title}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Created {created_count} blog posts without tags."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "These posts have NO tags - test auto-tagging to see if tags are added."
            )
        )
