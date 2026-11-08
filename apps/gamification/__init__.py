"""Gamification: XP, levels, badges and the leaderboard, driven by domain events.

Contract for other apps (``apps.gamification.api``): publish ``gamification.award_xp``
on the event bus or call ``api.award``. XP is the one reputation score: it is
mirrored into ``users.Reputation`` so the account dashboard shows the same number.
"""
