import re


_VALID_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def is_valid_slug(value: str) -> bool:
    return bool(_VALID_SLUG.fullmatch(value))
