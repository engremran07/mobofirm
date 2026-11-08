"""Credit bounties on firmware requests, models and variants.

A bounty holds the poster's credits in wallet escrow until staff approve an upload
that fulfils it (``firmware.approved`` event), then pays the uploader. Wallet
contract: ``apps.wallet.api``. Firmware catalogue: linked lazily through
ContentType, never imported.
"""
