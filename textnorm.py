"""Text normalization shared by the parser and the server."""
import re
import unicodedata


def norm(s: str) -> str:
    """Lowercase, strip diacritics/®/*, collapse spaces — for matching & search."""
    s = s.replace("ł", "l").replace("Ł", "L")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[®*™:]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()
