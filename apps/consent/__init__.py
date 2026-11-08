"""Cookie and tracking consent: the one source of truth for optional trackers.

Other apps ask ``apps.consent.services.has_consent(request, category)``; they never
read the consent cookie themselves.
"""
