#!/usr/bin/env python3
"""Parse McDonald's Poland nutrition + allergen PDF into data.json.

Usage:  python parse_pdf.py nutrient_table.pdf data.json
Needs:  pdfplumber (pip install -r requirements.txt)
"""
import json, re, sys, difflib

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
    items, section, category = [], None, None
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
            break
        if flat.startswith("100 g porcja"):
            in_data = True
            continue
        if not in_data:
            continue
        if line.startswith("*"):
            in_data = False
            continue
        name, toks = split_values(line)
        if not name and len(toks) >= N_VALUES and pending:
            # wrapped name: "prefix" / numbers / "suffix" on three lines; the suffix
            # line is attached by the lowercase-continuation rule below
            name, pending = " ".join(pending), []
        elif not name and toks:  # stray numbers on their own line, e.g. "0,06/0,1"
            stray = toks[0]
            continue
        if len(toks) == N_VALUES - 1 and name and stray:
            # Only case in the PDF: Coca-Cola Zero, salt portion "0,03/" + "0,06/0,1".
            # Salt per 100 ml is missing in the cell -> use 0.
            toks = toks[:-2] + ["0", toks[-2].split("/")[0] + "/" + stray, toks[-1]]
        stray = None if toks else stray
        if len(toks) >= N_VALUES and name:
            if len(toks) > N_VALUES:  # stray numbers belong to the name (e.g. "0,25l")
                extra = toks[: len(toks) - N_VALUES]
                name, toks = f"{name} {' '.join(extra)}", toks[-N_VALUES:]
            full = " ".join(pending + [name])
            if subgroup and full.startswith(subgroup_prefix):
                full += f" ({subgroup})"
            items.append(build_item(full, toks, category, section))
            pending = []
        elif not toks and is_category(line):
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
        elif not toks:
            pending.append(line)
        # lines like "0,06/0,1" (stray footnote numbers) are ignored
    return items


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


def main(pdf, out):
    with pdfplumber.open(pdf) as doc:
        text = "\n".join(page.extract_text(layout=True) for page in doc.pages)
    lines = text.splitlines()
    items = parse_nutrition(lines)
    allergens = parse_allergens(lines)
    keys = list(allergens)

    unmatched = []
    for i, it in enumerate(items, 1):
        it["id"] = i
        k = norm(it["name"])
        hit = allergens.get(k)
        if not hit:
            close = difflib.get_close_matches(k, keys, n=1, cutoff=0.9)
            hit = allergens.get(close[0]) if close else None
        it["allergens"] = {
            "contains": describe(hit["contains"]) if hit else [],
            "may_contain": describe(hit["may_contain"]) if hit else [],
        }
        if not hit:
            unmatched.append(it["name"])

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": pdf.split("/")[-1], "legend": ALLERGEN_LEGEND,
                   "items": items}, f, ensure_ascii=False, indent=1)
    print(f"Parsed {len(items)} items, {len(allergens)} allergen rows -> {out}")
    print(f"{len(unmatched)} items without an allergen row (the PDF says these have none)")


if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) >= 3 else ("nutrient_table.pdf", "data.json")))
