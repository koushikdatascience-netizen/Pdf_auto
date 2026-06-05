import argparse
import json
import mimetypes
import re
from pathlib import Path

import fitz


MONEY_RE = re.compile(r"^\d+(?:\.\d{2})$")
INT_RE = re.compile(r"^\d+$")
BATCH_RE = re.compile(r"\[FL/[^\]]+\]")


def clean_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def file_signature(path):
    with path.open("rb") as handle:
        return handle.read(8).decode("latin-1", errors="replace")


def normalize_join(parts):
    text = " ".join(part.strip() for part in parts if part.strip())
    text = re.sub(r"\s+", " ", text)
    text = text.replace("[FL/2022- 2023/", "[FL/2022-2023/")
    return text.strip()


def split_label_batch(text):
    text = normalize_join([text])
    match = BATCH_RE.search(text)
    if not match:
        return text, None
    label = normalize_join([text[: match.start()]])
    return label, match.group(0).strip("[]")


def parse_money(lines, start):
    values = []
    i = start
    while i < len(lines) and len(values) < 3:
        if MONEY_RE.match(lines[i]):
            values.append(float(lines[i]))
            i += 1
        else:
            break
    return values, i


def is_manufacturer_line(line):
    return (
        line.startswith("M/S ")
        or line.startswith("Inbrew ")
        or line.startswith("of M/s ")
        or "(FL-" in line
    )


def parse_items(lines):
    items = []
    groups = []
    current_group_lines = []
    group_start_item_index = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        if line == "Grand Total":
            break

        if line == "Total:":
            totals, next_i = parse_money(lines, i + 2)
            if i + 1 < len(lines) and INT_RE.match(lines[i + 1]) and len(totals) == 3:
                manufacturer = normalize_join(current_group_lines)
                group_items = items[group_start_item_index:]
                for item in group_items:
                    item["manufacturer"] = manufacturer
                groups.append(
                    {
                        "manufacturer": manufacturer,
                        "item_sl_numbers": [item["sl"] for item in group_items],
                        "total_cases": int(lines[i + 1]),
                        "total_duty": totals[0],
                        "total_mfg_amount": totals[1],
                        "total_vat": totals[2],
                    }
                )
                current_group_lines = []
                group_start_item_index = len(items)
                i = next_i
                continue

        if (is_manufacturer_line(line) or current_group_lines) and not INT_RE.match(line):
            current_group_lines.append(line)
            i += 1
            continue

        if INT_RE.match(line) and i + 1 < len(lines):
            serial = int(line)
            j = i + 1
            label_parts = []

            while j < len(lines):
                if INT_RE.match(lines[j]) and j + 2 < len(lines) and (
                    lines[j + 1].startswith("(") or lines[j + 1] in {"Bottle)"}
                ):
                    break
                label_parts.append(lines[j])
                j += 1

            if j >= len(lines):
                i += 1
                continue

            capacity_ml = int(lines[j])
            j += 1
            package_parts = []
            while j < len(lines) and not INT_RE.match(lines[j]):
                package_parts.append(lines[j].strip("()"))
                j += 1

            if j >= len(lines) or not INT_RE.match(lines[j]):
                i += 1
                continue

            quantity_cases = int(lines[j])
            amounts, next_i = parse_money(lines, j + 1)
            if len(amounts) != 3:
                i += 1
                continue

            label, batch = split_label_batch(normalize_join(label_parts))
            item = {
                "sl": serial,
                "label_name": label,
                "batch": batch,
                "capacity_ml": capacity_ml,
                "package": normalize_join(package_parts),
                "quantity_cases": quantity_cases,
                "duty": amounts[0],
                "mfg_amount": amounts[1],
                "vat": amounts[2],
            }
            items.append(item)
            i = next_i
            continue

        # Page-break continuations can appear after the numeric row values.
        if items and not is_manufacturer_line(line):
            if line == "Bottle)":
                items[-1]["package"] = normalize_join([items[-1]["package"], "Bottle"])
            elif line.startswith(")") or line.startswith("[FL/"):
                combined = normalize_join([items[-1]["label_name"], line])
                label, batch = split_label_batch(combined)
                items[-1]["label_name"] = label
                items[-1]["batch"] = batch or items[-1]["batch"]

        i += 1

    return items, groups


def parse_header(lines):
    full_text = "\n".join(lines)
    demand = re.search(r"Demand Id-\s*([^\n]+)", full_text)
    date = re.search(r"Date-\s*([^\n]+)", full_text)
    licensee = re.search(r"Licensee Name-\s*(.*?),\s*Shop Name\s*:\s*([^\n]+)", full_text)
    return {
        "department": lines[0] if lines else None,
        "document_type": lines[1] if len(lines) > 1 else None,
        "warehouse": lines[2] if len(lines) > 2 else None,
        "licensee_name": licensee.group(1).strip() if licensee else None,
        "shop_name": licensee.group(2).strip() if licensee else None,
        "demand_id": demand.group(1).strip() if demand else None,
        "date": date.group(1).strip() if date else None,
    }


def parse_footer(lines):
    text = "\n".join(lines)
    fields = {
        "online_payment_reference_number": r"Online Payment Reference Number\s*:\s*([^\n]+)",
        "dc_number": r"DC Number:\s*\n?([^\n]+)",
        "manufacturing_amount": r"Manufacturing Amount\s*:\s*([0-9.]+)",
        "income_tax": r"Income Tax\s*:\s*([0-9.]+)",
        "vat_amount": r"VAT Amount\s*:\s*([0-9.]+)",
        "grand_total_amount": r"Grand Total\s*:\s*([0-9.]+)",
    }
    result = {}
    for key, pattern in fields.items():
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            result[key] = float(value) if re.fullmatch(r"[0-9.]+", value) else value
    return result


def parse_grand_total(lines):
    for i, line in enumerate(lines):
        if line == "Grand Total" and i + 4 < len(lines):
            return {
                "quantity_cases": int(lines[i + 1]),
                "duty": float(lines[i + 2]),
                "mfg_amount": float(lines[i + 3]),
                "vat": float(lines[i + 4]),
            }
    return None


def extract_pdf(path):
    doc = fitz.open(path)
    try:
        page_texts = [page.get_text("text") for page in doc]
        lines = clean_lines("\n".join(page_texts))
        items, groups = parse_items(lines)

        return {
            "source_file": str(path),
            "file_type": {
                "extension": path.suffix.lower(),
                "mime_guess": mimetypes.guess_type(path.name)[0],
                "signature": file_signature(path),
                "pdf_format": doc.metadata.get("format"),
            },
            "metadata": doc.metadata,
            "page_count": doc.page_count,
            "extraction_method": "native_pdf_text_pymupdf",
            "needs_ocr": all(len(text.strip()) < 20 for text in page_texts),
            "header": parse_header(lines),
            "items": items,
            "manufacturer_groups": groups,
            "grand_total": parse_grand_total(lines),
            "payment": parse_footer(lines),
            "raw_pages": [
                {"page": index + 1, "text": text.strip()}
                for index, text in enumerate(page_texts)
            ],
        }
    finally:
        doc.close()


def main():
    parser = argparse.ArgumentParser(description="Extract delivery challan PDFs to JSON.")
    parser.add_argument("pdf", nargs="*", type=Path, default=list(Path(".").glob("*.pdf")))
    parser.add_argument("--out-dir", type=Path, default=Path("json_output"))
    args = parser.parse_args()

    args.out_dir.mkdir(exist_ok=True)
    for pdf_path in args.pdf:
        result = extract_pdf(pdf_path)
        out_path = args.out_dir / f"{pdf_path.stem}.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out_path}")


def extract_to_json_text(pdf_path):
    return json.dumps(extract_pdf(Path(pdf_path)), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
