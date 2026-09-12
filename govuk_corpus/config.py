"""Pilot configuration for the GOV.UK corpus rebuild."""
from __future__ import annotations

GOVUK_API_BASE = "https://www.gov.uk/api/content"
GOVUK_SITEMAP_INDEX = "https://www.gov.uk/sitemap.xml"

# Politeness: be gentle on the public API.
RATE_LIMIT_PER_SEC = 2.0
REQUEST_TIMEOUT = 30.0
USER_AGENT = "gov-uk-corpus/0.1 (+contact: corpus rebuild pilot)"

# Stage 2 — redirect chains. GOV.UK chains are short; cap to catch loops/pathologies.
MAX_REDIRECT_DEPTH = 10

# DEFRA-family publishing bodies (pilot scope, Q1). Mirrors the 44 slugs in
# ai-eu-trade-accelerator/guidance-discovery/src/filters.py.
DEFRA_ORGS = [
    "animal-and-plant-health-agency",
    "centre-for-environment-fisheries-and-aquaculture-science",
    "department-for-environment-food-rural-affairs",
    "environment-agency",
    "forestry-commission",
    "marine-management-organisation",
    "natural-england",
    "rural-payments-agency",
    "veterinary-medicines-directorate",
]

# A tiny DEFRA seed frontier for the offline/first-run pilot. Deliberately mixed:
# a bare-host URL (canonicalises + dedupes against its www twin) and a malformed
# one (must be rejected on ingest) to exercise the canonicalisation gate.
PILOT_SEED_URLS = [
    "https://www.gov.uk/guidance/storing-silage-slurry-and-agricultural-fuel-oil",
    "https://gov.uk/guidance/storing-silage-slurry-and-agricultural-fuel-oil",  # dup of above after canonicalisation
    "https://www.gov.uk/guidance/rules-for-farmers-and-land-managers-to-prevent-water-pollution",
    "https://www.gov.uk/guidance/nutrient-management-nitrate-vulnerable-zones",
    "https://www.gov.uk/guidance/using-nitrogen-fertilisers-in-nitrate-vulnerable-zones",
    "https://www.gov.uk/government/publications/slurry-infrastructure-grant-round-2-guidance-for-invited-applicants",
    "https://gov.ukhttps://webarchive.nationalarchives.gov.uk/ukgwa/x",  # malformed -> rejected
]
