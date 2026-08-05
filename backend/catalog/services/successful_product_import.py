import csv
import io

from django.db import transaction

from catalog.models import SuccessfulProduct
from catalog.services.normalization import normalize_title


CSV_HEADERS = ("title", "category", "keywords")
MAX_CSV_FILE_SIZE = 1024 * 1024
TITLE_MAX_LENGTH = SuccessfulProduct._meta.get_field("title").max_length
CATEGORY_MAX_LENGTH = SuccessfulProduct._meta.get_field("category").max_length


class SuccessfulProductImportError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def normalize_keywords(values: list[str]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = collapse_whitespace(value)
        if not keyword:
            raise ValueError("keywords cannot contain blank items")
        key = keyword.casefold()
        if key not in seen:
            seen.add(key)
            keywords.append(keyword)
    return keywords


def parse_successful_products_csv(uploaded_file) -> list[dict[str, object]]:
    filename = str(getattr(uploaded_file, "name", ""))
    if not filename.lower().endswith(".csv"):
        raise SuccessfulProductImportError(["Upload must be a .csv file."])

    file_size = getattr(uploaded_file, "size", 0)
    if file_size > MAX_CSV_FILE_SIZE:
        raise SuccessfulProductImportError(
            [f"CSV file must not exceed {MAX_CSV_FILE_SIZE} bytes."]
        )

    payload = uploaded_file.read()
    if not payload:
        raise SuccessfulProductImportError(["CSV file is empty."])
    if len(payload) > MAX_CSV_FILE_SIZE:
        raise SuccessfulProductImportError(
            [f"CSV file must not exceed {MAX_CSV_FILE_SIZE} bytes."]
        )

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SuccessfulProductImportError(
            ["CSV file must be encoded as UTF-8."]
        ) from exc
    if not text.strip():
        raise SuccessfulProductImportError(["CSV file is empty."])
    if "\x00" in text:
        raise SuccessfulProductImportError(["CSV file contains invalid null bytes."])

    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = reader.fieldnames or []
    except csv.Error as exc:
        raise SuccessfulProductImportError([f"Malformed CSV: {exc}."]) from exc
    missing_headers = [header for header in CSV_HEADERS if header not in headers]
    extra_headers = [header for header in headers if header not in CSV_HEADERS]
    duplicate_headers = sorted(
        {header for header in headers if headers.count(header) > 1}
    )
    header_errors: list[str] = []
    if missing_headers:
        header_errors.append(
            f"Missing required headers: {', '.join(missing_headers)}."
        )
    if extra_headers:
        header_errors.append(f"Unexpected headers: {', '.join(extra_headers)}.")
    if duplicate_headers:
        header_errors.append(f"Duplicate headers: {', '.join(duplicate_headers)}.")
    if header_errors:
        raise SuccessfulProductImportError(header_errors)

    parsed_rows: list[dict[str, object]] = []
    errors: list[str] = []
    seen_keys: dict[tuple[str, str], int] = {}
    try:
        for row in reader:
            row_number = reader.line_num
            if None in row or any(value is None for value in row.values()):
                errors.append(f"Row {row_number}: column count does not match headers.")
                continue
            if all(not value.strip() for value in row.values()):
                errors.append(f"Row {row_number}: data row is empty.")
                continue

            title = collapse_whitespace(row["title"])
            category = collapse_whitespace(row["category"])
            row_errors: list[str] = []
            if not title:
                row_errors.append("title is required")
            elif len(title) > TITLE_MAX_LENGTH:
                row_errors.append(
                    f"title must not exceed {TITLE_MAX_LENGTH} characters"
                )
            if not category:
                row_errors.append("category is required")
            elif len(category) > CATEGORY_MAX_LENGTH:
                row_errors.append(
                    f"category must not exceed {CATEGORY_MAX_LENGTH} characters"
                )

            normalized_title = normalize_title(title)
            if title and not normalized_title:
                row_errors.append("title must contain letters or numbers")

            raw_keywords = row["keywords"].strip()
            try:
                keywords = (
                    normalize_keywords(raw_keywords.split(";"))
                    if raw_keywords
                    else []
                )
            except ValueError as exc:
                row_errors.append(str(exc))

            if row_errors:
                errors.extend(
                    f"Row {row_number}: {message}." for message in row_errors
                )
                continue

            natural_key = (normalized_title, category)
            if natural_key in seen_keys:
                errors.append(
                    f"Row {row_number}: duplicates row {seen_keys[natural_key]} "
                    "by normalized title and category."
                )
                continue
            seen_keys[natural_key] = row_number
            parsed_rows.append(
                {
                    "title": title,
                    "normalized_title": normalized_title,
                    "category": category,
                    "keywords": keywords,
                }
            )
    except csv.Error as exc:
        errors.append(f"Row {reader.line_num}: malformed CSV: {exc}.")

    if not parsed_rows and not errors:
        errors.append("CSV file must contain at least one data row.")
    if errors:
        raise SuccessfulProductImportError(errors)
    return parsed_rows


def import_successful_products(uploaded_file) -> dict[str, int]:
    rows = parse_successful_products_csv(uploaded_file)
    created_count = 0
    updated_count = 0
    with transaction.atomic():
        for row in rows:
            _, created = SuccessfulProduct.objects.update_or_create(
                normalized_title=row["normalized_title"],
                category=row["category"],
                defaults={
                    "title": row["title"],
                    "keywords": row["keywords"],
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "total_count": len(rows),
    }
