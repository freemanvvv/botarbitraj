"""
Скачивает все PDF/HTML из sources.csv в data/normbase_raw/.

После выполнения индексация работает полностью офлайн:
  python src/main.py nb-index

Использование:
  python scripts/download_normatives.py             # скачать всё новое
  python scripts/download_normatives.py --force     # перескачать даже уже существующие
  python scripts/download_normatives.py --only kmk-2.02.01-98  # один документ
  python scripts/download_normatives.py --dry-run   # показать что будет скачано без скачивания
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import requests

# Пути
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
SOURCES_CSV = PROJECT_DIR / "src" / "normbase" / "sources.csv"
RAW_DIR = PROJECT_DIR / "data" / "normbase_raw"

USER_AGENT = "ConstructionAICopilot/1.0"
REQUEST_TIMEOUT = 60
DELAY_BETWEEN_REQUESTS = 1.5  # секунд между запросами (вежливо к серверу)


def detect_ext(url: str, content_type: str) -> str:
    url_lower = url.lower()
    if url_lower.endswith(".pdf") or "pdf" in content_type:
        return ".pdf"
    if url_lower.endswith((".html", ".htm")) or "html" in content_type:
        return ".html"
    return ".bin"


def download_file(doc_id: str, url: str, force: bool = False) -> tuple[str, str]:
    """
    Скачивает файл по URL в RAW_DIR/{doc_id}.{ext}.
    Возвращает (статус, путь_или_ошибка).
    Статусы: 'ok', 'skip', 'error'
    """
    # Определяем расширение по URL (окончательно уточним после HEAD/GET)
    ext_guess = ".pdf" if url.lower().endswith(".pdf") else ".bin"
    dest_guess = RAW_DIR / f"{doc_id}{ext_guess}"

    # Уже скачан?
    if not force:
        for ext in (".pdf", ".html", ".bin"):
            existing = RAW_DIR / f"{doc_id}{ext}"
            if existing.exists() and existing.stat().st_size > 1024:
                return "skip", str(existing)

    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            stream=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        ext = detect_ext(url, content_type)
        dest = RAW_DIR / f"{doc_id}{ext}"

        with open(dest, "wb") as f:
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                total += len(chunk)

        if total < 512:
            dest.unlink(missing_ok=True)
            return "error", f"файл слишком мал ({total} байт) — вероятно страница-заглушка"

        return "ok", str(dest)

    except requests.exceptions.HTTPError as e:
        return "error", f"HTTP {e.response.status_code}"
    except requests.exceptions.ConnectionError:
        return "error", "нет соединения"
    except requests.exceptions.Timeout:
        return "error", "таймаут"
    except Exception as e:
        return "error", str(e)


def main():
    parser = argparse.ArgumentParser(description="Скачать нормативы из sources.csv")
    parser.add_argument("--force", action="store_true", help="перескачать существующие файлы")
    parser.add_argument("--only", metavar="ID", help="скачать только документ с этим id")
    parser.add_argument("--dry-run", action="store_true", help="показать список без скачивания")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Читаем CSV
    rows = []
    with open(SOURCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("source_url") or "").strip()
            if not url:
                continue
            if args.only and row["id"] != args.only:
                continue
            rows.append(row)

    total = len(rows)
    print(f"Документов со ссылками: {total}")

    if args.dry_run:
        print("\nСписок документов для скачивания:\n")
        for row in rows:
            existing = any(
                (RAW_DIR / f"{row['id']}{ext}").exists()
                for ext in (".pdf", ".html", ".bin")
            )
            mark = "✅ есть" if existing else "⬜ нет"
            label = f"{row.get('doc_type','')} {row.get('number','')} — {row.get('title','')[:60]}"
            print(f"  {mark}  [{row['id']}] {label}")
        return

    ok = skip = errors = 0
    error_list = []

    print()
    for i, row in enumerate(rows, 1):
        doc_id = row["id"]
        url = row["source_url"].strip()
        label = f"{row.get('doc_type','')} {row.get('number','')}"
        prefix = f"[{i}/{total}] {doc_id} — {label}"

        status, detail = download_file(doc_id, url, force=args.force)

        if status == "ok":
            size_kb = Path(detail).stat().st_size // 1024
            print(f"  ✅ {prefix} ({size_kb} КБ)")
            ok += 1
            time.sleep(DELAY_BETWEEN_REQUESTS)
        elif status == "skip":
            size_kb = Path(detail).stat().st_size // 1024
            print(f"  ⏭️  {prefix} — уже есть ({size_kb} КБ)")
            skip += 1
        else:
            print(f"  ❌ {prefix} — {detail}")
            error_list.append((doc_id, url, detail))
            errors += 1

    print(f"\n{'='*60}")
    print(f"✅ Скачано:      {ok}")
    print(f"⏭️  Пропущено:   {skip}  (уже существовали)")
    print(f"❌ Ошибок:      {errors}")
    print(f"{'='*60}")

    if error_list:
        print("\nПроблемные документы (можно скачать вручную):")
        for doc_id, url, reason in error_list:
            print(f"  {doc_id}: {reason}")
            print(f"    {url}")

    if ok > 0:
        print(f"\nФайлы сохранены в: {RAW_DIR}")
        print("Теперь индексация работает офлайн:")
        print("  python src/main.py nb-index")


if __name__ == "__main__":
    main()
