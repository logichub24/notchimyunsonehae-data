#!/usr/bin/env python3
"""Small, offline checks for the generated-data normalizer."""
from refresh_data import iso_date, period_end, target_types

assert iso_date("2026. 08. 17") == "2026-08-17"
assert iso_date("2026-8-17") == ""
assert period_end("2026.08.01 ~ 2026.08.31") == "2026-08-31"
assert period_end("상시") == ""
assert target_types("청년 주택 취업 지원") == ["house", "job"]
print("refresh-data checks passed")
