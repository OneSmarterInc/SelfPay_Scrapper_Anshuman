"""Contact-list identity, membership and non-destructive additions."""
import re

PHONE_FIELD = 'Current supported call number'
EMAIL_FIELD = 'Current usable email'
MULTI_FIELDS = (PHONE_FIELD, EMAIL_FIELD)
EMAIL = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.I)
PHONE = re.compile(r'(?<!\d)(?:\+?1[\s.\-]*)?(?:\(\d{3}\)|\d{3})[\s.\-]*\d{3}[\s.\-]*\d{4}(?:\s*(?:;ext=|extension|ext\.?|x)\s*\d{1,6})?(?!\d)', re.I)


def identity(field, value):
    value = str(value).strip()
    if field == EMAIL_FIELD:
        return value.casefold()
    parts = re.split(r'\s*(?:;ext=|extension|ext\.?|x)\s*', value, maxsplit=1, flags=re.I)
    digits = re.sub(r'\D', '', parts[0])
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits + (':ext:' + re.sub(r'\D', '', parts[1]) if len(parts) > 1 else '')


def values(field, value):
    pattern = EMAIL if field == EMAIL_FIELD else PHONE
    found = pattern.findall(str(value or ''))
    unique = {}
    for item in found:
        unique.setdefault(identity(field, item), item.lower() if field == EMAIL_FIELD else item)
    return list(unique.values())


def contains(field, existing, candidate):
    proposed = values(field, candidate)
    known = {identity(field, item) for item in values(field, existing)}
    return bool(proposed) and all(identity(field, item) in known for item in proposed)


def append(field, existing, candidate):
    # Keep existing labels and roles; only append genuinely new contact values.
    known = {identity(field, item) for item in values(field, existing)}
    additions = [item for item in values(field, candidate) if identity(field, item) not in known]
    return '\n'.join(([str(existing).strip()] if str(existing).strip() else []) + additions)
