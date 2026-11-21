#!/usr/bin/env python3
"""Parse McDonald's Poland nutrition + allergen PDF into data.json.

Usage:  python parse_pdf.py [nutrition_table.pdf] [data.json] [--force] [--check]
Needs:  pdfplumber (pip install -r requirements.txt)

The parser validates its own output and exits with an error (writing nothing) when
the PDF doesn't look like what it was written for. It also keeps products.lock, a
readable list of every product, to report what changed between PDF versions.
products.lock starts with the PDF's SHA-256; --check (used in the Docker build) fails
when the PDF or the parser output no longer matches the committed lock.
"""
import argparse, hashlib, json, re, sys, difflib
from collections import Counter
from pathlib import Path

import pdfplumber

from textnorm import norm

# Column layout of every nutrition row: 26 numbers.
# Each nutrient has (per 100 g, per portion, % of RI) except fiber (no RI).
NUTRIENTS = [
    ("energy_kj", True), ("energy_kcal", True), ("fat_g", True),
    ("saturated_fat_g", True), ("carbs_g", True), ("sugars_g", True),
    ("fiber_g", False), ("protein_g", True), ("salt_g", True),
]
N_VALUES = sum(3 if ri else 2 for _, ri in NUTRIENTS)  # 26

ALLERGEN_LEGEND = {
    "1": "gluten", "1a": "gluten (wheat)", "1b": "gluten (rye)", "1c": "gluten (barley)",
    "1d": "gluten (oat)", "1e": "gluten (spelt)", "1f": "gluten (khorasan wheat)",
    "2": "crustaceans", "3": "eggs", "4": "fish", "5": "peanuts", "6": "soy", "7": "milk",
    "8": "nuts", "8a": "almonds", "8b": "hazelnuts", "8c": "walnuts", "8d": "cashews",
    "8e": "pecans", "8f": "brazil nuts", "8g": "pistachios", "8h": "macadamia",
    "8i": "queensland nuts", "9": "celery", "10": "mustard", "11": "sesame",
    "12": "sulfur dioxide", "13": "lupin", "14": "mollusks",
}

# English names for the PDF's section headings, keyed by norm(heading).
# Unknown headings (e.g. in a future PDF) fall back to the Polish text.
CATEGORY_EN = {
    "oferta klasyczna": "Classic menu",
    "salatki": "Salads",
    "sosy do salatek": "Salad dressings",
    "ekstrakaski": "Snacks",
    "oferta sniadaniowa": "Breakfast",
    "frytki": "Fries",
    "dodatki": "Sides & sauces",
    "produkty promocyjne": "Limited-time offers",
    "desery": "Desserts",
    "napoje": "Drinks",
    "mccafe": "McCafé",
}

NUM = r"-?\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)*"
NUM_RE = re.compile(rf"^{NUM}$")
CODE_RE = re.compile(r"^\d{1,2}[a-i]?(?:-[a-i])?$")


def to_num(tok: str):
    first = tok.split("/")[-1].replace(",", ".")  # sized values: report the largest
    return float(first) if "." in first else int(first)


def is_category(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters) and not re.search(r"\d", line)


def split_values(line: str):
    """Return (name_part, values) where values are the trailing numeric tokens."""
    toks = line.split()
    i = len(toks)
    while i > 0 and NUM_RE.match(toks[i - 1]):
        i -= 1
    return " ".join(toks[:i]), toks[i:]


def build_item(name, toks, category_pl, section):
    vals = iter(toks)
    nutr = {}
    for key, has_ri in NUTRIENTS:
        per100, portion = next(vals), next(vals)
        ri = next(vals) if has_ri else None
        nutr[key] = {
            "per_100g": to_num(per100),
            "per_portion": to_num(portion),
            **({"ri_percent": to_num(ri)} if has_ri else {}),
        }
        if "/" in per100 + portion:  # e.g. Coca-Cola Zero "3/5/6" = 0,25l/0,4l/0,5l
            nutr[key]["per_portion_by_size"] = [to_num(x) for x in portion.split("/")]
    name = re.sub(r"\s+", " ", name).strip()
    return {
        "name": name.rstrip("*").strip(),
        "category": CATEGORY_EN.get(norm(category_pl), category_pl),
        "category_pl": category_pl,
        "section": section,
        "limited_availability": name.endswith("*") and not name.endswith("**"),
        "nutrition": nutr,
    }


def parse_nutrition(lines):
    """Return (items, problems). Problems are rows the parser couldn't interpret."""
    items, problems, section, category = [], [], None, None
    in_data, pending, subgroup, subgroup_prefix, stray = False, [], None, "", None
    for raw in lines:
        line = re.sub(r"\s+/(?=\d)", "/", raw.strip())  # "0,03 /0,1" -> "0,03/0,1"
        if not line:
            continue
        flat = re.sub(r"\s+", " ", line)
        if re.match(r"^/\d", line):  # tail of a split cell (Coca-Cola Zero salt), handled below
            continue
        if flat.startswith("TABELA WARTO"):
            section = "McCafé" if "McCAF" in flat else "McDonald's"
            category = "McCafé" if section == "McCafé" else None
            in_data = False
            continue
        if flat.startswith("TABELA ALERGEN"):
            if pending:
                problems.append(f"product name without values: {' '.join(pending)!r}")
            break
        if flat.startswith("100 g porcja"):
            in_data = True
            continue
        if not in_data:
            continue
        if line.startswith("*"):
            if pending:
                problems.append(f"product name without values: {' '.join(pending)!r}")
            in_data, pending = False, []
            continue
        name, toks = split_values(line)
        if not name and len(toks) >= N_VALUES and pending:
            # wrapped name: "prefix" / numbers / "suffix" on three lines; the suffix
            # line is attached by the lowercase-continuation rule below
            name, pending = " ".join(pending), []
        elif not name and len(toks) <= 2:  # stray cell fragment, e.g. "0,06/0,1"
            stray = toks[0]
            continue
        elif not name:
            problems.append(f"values without a product name ({len(toks)} numbers): {flat!r}")
            continue
        if len(toks) == N_VALUES - 1 and name and stray:
            # Only case in the PDF: Coca-Cola Zero, salt portion "0,03/" + "0,06/0,1".
            # Salt per 100 ml is missing in the cell -> use 0.
            toks = toks[:-2] + ["0", toks[-2].split("/")[0] + "/" + stray, toks[-1]]
        stray = None if toks else stray
        if name and 3 <= len(toks) < N_VALUES:
            problems.append(f"expected {N_VALUES} values, found {len(toks)}: {flat!r}")
            pending = []
            continue
        if len(toks) >= N_VALUES and name:
            if len(toks) > N_VALUES:  # stray numbers belong to the name (e.g. "0,25l")
                extra = toks[: len(toks) - N_VALUES]
                name, toks = f"{name} {' '.join(extra)}", toks[-N_VALUES:]
            full = " ".join(pending + [name])
            if subgroup and full.startswith(subgroup_prefix):
                full += f" ({subgroup})"
            if category is None:
                problems.append(f"product before any category heading: {full!r}")
            items.append(build_item(full, toks, category or "?", section))
            pending = []
        elif not toks and is_category(line):
            if pending:
                problems.append(f"product name without values: {' '.join(pending)!r}")
            category, pending, subgroup = line.title(), [], None
        elif not toks and line.endswith(":"):
            # "Lody o smaku waniliowym w kubeczku:" -> label "w kubeczku" for rows starting "Lody"
            subgroup = line.rstrip(":").split("waniliowym")[-1].strip() or line.rstrip(":")
            subgroup_prefix = line.split()[0]
        elif not toks and line[0].islower() and not pending and items:
            # continuation of the previous item's name (name wrapped below the numbers)
            items[-1]["name"] = re.sub(r"\s+", " ", f"{items[-1]['name']} {line}")
            if subgroup and items[-1]["name"].startswith(subgroup_prefix):
                items[-1]["name"] = items[-1]["name"].replace(f" ({subgroup})", "") + f" ({subgroup})"
        elif not toks and re.search(r"[^\W\d_]", line):  # has letters: part of a name
            pending.append(line)
        # anything else (e.g. "(%)" left over from the header) is ignored
    return items, problems


def parse_allergens(raw_lines):
    """Rows: name | contains codes | may-contain codes (columns split by 2+ spaces)."""
    result, active, pending, subgroup, subgroup_prefix, last = {}, False, [], None, "", None
    for raw in raw_lines:
        if "TABELA ALERGEN" in re.sub(r"\s+", " ", raw):
            active = True
            continue
        if not active:
            continue
        line = raw.strip()
        if not line or line.startswith(("ALERGENY", "Produkty z", "poniżej", "*")):
            if line.startswith("*"):
                active = False
            continue
        if line.startswith("Pełna lista"):
            break
        cols = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]
        # move trailing code-groups out of the name column
        name_parts, code_cols, raw_cols = [], [], []
        for c in cols:
            toks = [t for t in re.split(r"[,\s]+", c) if t]
            if toks and all(CODE_RE.match(t) for t in toks):
                code_cols.append(toks)
                raw_cols.append(c)
            else:
                # name column might end with codes when separated by one space
                m = re.match(r"^(.*?[^\d\s,])((?:\s+\d{1,2}[a-i]?(?:-[a-i])?,?)+)$", c)
                if m and not code_cols:
                    name_parts.append(m.group(1))
                    code_cols.append([t for t in re.split(r"[,\s]+", m.group(2)) if t])
                    raw_cols.append(m.group(2))
                else:
                    name_parts.append(c)
        name = " ".join(name_parts)
        if is_category(line) and not code_cols:
            subgroup, pending = None, []
            continue
        if name.endswith(":") and not code_cols:
            subgroup = name.rstrip(":").split("waniliowym")[-1].strip()
            subgroup_prefix = name.split()[0]
            continue
        if not code_cols:
            if name and name[0].islower() and last and not pending:
                # suffix of previous entry
                entry = result.pop(last)
                last = norm(f"{entry['_name']} {name}")
                entry["_name"] = f"{entry['_name']} {name}"
                result[last] = entry
            else:
                pending.append(name)
            continue
        full = " ".join(pending + ([name] if name else []))
        if subgroup and full.startswith(subgroup_prefix):
            full += f" ({subgroup})"
        pending = []
        groups, cur = [], []
        for tok in re.findall(r"[\w-]+,?", " ".join(raw_cols)):
            cur.append(tok.rstrip(","))
            if not tok.endswith(","):
                groups.append(cur); cur = []
        if cur:
            groups.append(cur)
        entry = {"_name": full, "contains": groups[0],
                 "may_contain": [c for g in groups[1:] for c in g]}
        last = norm(full)
        result[last] = entry
    return result


def expand_codes(codes):
    """'1a-b' -> ['1a','1b'], '8a-h' -> 8a..8h."""
    out = []
    for c in codes:
        m = re.match(r"^(\d+)([a-i])-([a-i])$", c)
        if m:
            out += [f"{m[1]}{chr(x)}" for x in range(ord(m[2]), ord(m[3]) + 1)]
        else:
            out.append(c)
    return out


def describe(codes):
    return [{"code": c, "name": ALLERGEN_LEGEND.get(c, c)} for c in expand_codes(codes)]


MAX_REMOVED_SHARE = 0.20  # fail if more than 20% of the products in products.lock disappear


def fingerprint(it):
    """Short hash of everything except the id, to spot changed values."""
    body = json.dumps([it["nutrition"], it["allergens"]], sort_keys=True)
    return hashlib.sha1(body.encode()).hexdigest()[:8]


def lock_lines(items):
    return [f"{it['category']}\t{it['name']}\t{it['nutrition']['energy_kcal']['per_portion']}"
            f"\t{fingerprint(it)}" for it in items]


def keyed(lines):
    """(category, name, n-th occurrence) -> line; some names appear twice, e.g. Ketchup."""
    seen, out = Counter(), {}
    for ln in lines:
        if ln.startswith("#"):
            continue
        cat, name = ln.split("\t")[:2]
        seen[(cat, name)] += 1
        out[(cat, name, seen[(cat, name)])] = ln
    return out


def report(old_lines, new_lines):
    old, new = keyed(old_lines), keyed(new_lines)
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k] != old[k]]
    label = lambda k: f"{k[1]} ({k[0]})"
    print(f"Compared with products.lock: {len(added)} new, {len(removed)} removed, "
          f"{len(changed)} changed")
    for k in added:
        print(f"  + {label(k)}")
    for k in removed:
        print(f"  - {label(k)}")
    for k in changed:
        o, n = old[k].split("\t")[2], new[k].split("\t")[2]
        print(f"  ~ {label(k)}" + (f": {o} -> {n} kcal" if o != n else ": values changed"))
    return removed


LOCK_HEADER = "# pdf-sha256: "


def read_lock(lock):
    """Return (pdf hash or None, product lines)."""
    if not lock.exists():
        return None, []
    lines = lock.read_text(encoding="utf-8").splitlines()
    pdf_hash = lines[0][len(LOCK_HEADER):] if lines and lines[0].startswith(LOCK_HEADER) else None
    return pdf_hash, [ln for ln in lines if ln and not ln.startswith("#")]


def fail(errors, hint="If the PDF layout changed, parse_pdf.py needs updating.",
         title="the parsed data failed validation"):
    print(f"\nERROR: {title} ({len(errors)} problems). Nothing was written.", file=sys.stderr)
    for e in errors[:15]:
        print(f"  - {e}", file=sys.stderr)
    if len(errors) > 15:
        print(f"  … and {len(errors) - 15} more", file=sys.stderr)
    print(hint, file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", nargs="?", default="nutrition_table.pdf")
    ap.add_argument("out", nargs="?", default="data.json")
    ap.add_argument("--lock", default=str(Path(__file__).with_name("products.lock")))
    ap.add_argument("--force", action="store_true",
                    help="accept the result even if many products disappeared")
    ap.add_argument("--check", action="store_true",
                    help="CI mode: require products.lock to match this PDF and the parser "
                         "output; never rewrites the lock")
    args = ap.parse_args()

    with pdfplumber.open(args.pdf) as doc:
        text = "\n".join(page.extract_text(layout=True) for page in doc.pages)
    lines = text.splitlines()
    items, errors = parse_nutrition(lines)
    allergens = parse_allergens(lines)
    keys = list(allergens)

    used, no_allergens = set(), []
    for i, it in enumerate(items, 1):
        it["id"] = i
        k = norm(it["name"])
        if k not in allergens:
            close = difflib.get_close_matches(k, keys, n=1, cutoff=0.9)
            k = close[0] if close else None
        hit = allergens.get(k) if k else None
        used.add(k)
        it["allergens"] = {
            "contains": describe(hit["contains"]) if hit else [],
            "may_contain": describe(hit["may_contain"]) if hit else [],
        }
        if not hit:
            no_allergens.append(it["name"])

    # Check: every row of the allergen table must belong to a product. If one doesn't,
    # names drifted apart and that product would silently show "no allergens".
    for k in keys:
        if k not in used:
            errors.append(f"allergen row matches no product: {allergens[k]['_name']!r}")
    if not allergens:
        errors.insert(0, "allergen table not found (heading changed?)")
    if not items:
        errors.insert(0, "no products found (table headings changed?)")
    if errors:
        fail(errors)

    print(f"Parsed {len(items)} products, {len(allergens)} allergen rows; "
          f"{len(no_allergens)} products have no allergens listed")

    lock = Path(args.lock)
    new_lines = lock_lines(items)
    pdf_hash = hashlib.sha256(Path(args.pdf).read_bytes()).hexdigest()
    old_hash, old_lines = read_lock(lock)

    if args.check:
        stale = "Run `python parse_pdf.py` locally, review the report and commit products.lock."
        if old_hash != pdf_hash:
            report(old_lines, new_lines)
            fail(["nutrition_table.pdf changed but products.lock wasn't updated for it"], stale,
                 "products.lock is out of date")
        if old_lines != new_lines:
            report(old_lines, new_lines)
            fail(["same PDF, but the parser output differs from products.lock "
                  "(parse_pdf.py or textnorm.py changed?)"], stale, "products.lock is out of date")
        print("products.lock matches the PDF and the parser output")
    elif old_lines:
        removed = report(old_lines, new_lines)
        if old_lines and len(removed) / len(old_lines) > MAX_REMOVED_SHARE and not args.force:
            fail([f"{len(removed)} of {len(old_lines)} products disappeared (limit "
                  f"{MAX_REMOVED_SHARE:.0%}). If that's real, re-run with --force."])

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"source": Path(args.pdf).name, "legend": ALLERGEN_LEGEND,
                   "items": items}, f, ensure_ascii=False, indent=1)
    if args.check:
        print(f"Wrote {args.out}")
        return
    lock.write_text(LOCK_HEADER + pdf_hash + "\n" + "\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} and {lock.name}")


if __name__ == "__main__":
    main()
