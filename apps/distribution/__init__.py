"""Automatic distribution and growth engine for newly approved firmware and posts.

Sixteen platform adapters (webhook, Telegram, Discord, Slack, X, Mastodon, Bluesky,
Facebook, Instagram, Threads, LinkedIn, Reddit, Pinterest, TikTok, YouTube Shorts,
WhatsApp) sit behind one registry in ``channels``/``platforms``. Channels are off until an
operator configures and enables them. All outbound HTTP goes through ``apps.seo.fetcher``'s
SafeFetcher (public https hosts only). Video needs ffmpeg on PATH; without it, video
platforms report "needs media" instead of pretending to post. See
``docs/04-backend/distribution.md``.
"""
