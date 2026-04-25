"""Baixa os PDFs dos editais abertos da FAPES (Espirito Santo).

Percorre as paginas de cada area, identifica cada edital aberto pelo
accordion (span.paneltitle-value) e baixa o PDF principal correspondente.
Os arquivos sao salvos em ./editais_fapes/<categoria>/.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://fapes.es.gov.br"

PAGES = [
    ("formacao_cientifica",     f"{BASE_URL}/edital-aberto-forma%C3%A7%C3%A3o-cient%C3%ADfica"),
    ("pesquisa",                f"{BASE_URL}/editais-abertos-pesquisa-4"),
    ("difusao_do_conhecimento", f"{BASE_URL}/difusao-do-conhecimento"),
    ("extensao",                f"{BASE_URL}/editais-abertos-extensao-2"),
    ("inovacao",                f"{BASE_URL}/inovacao"),
    ("chamadas_internacionais", f"{BASE_URL}/chamadas-internacionais"),
]

OUTPUT_DIR = Path(__file__).resolve().parent / "editais_fapes"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 180
PAUSE_SECONDS = 1.0


def sanitize_filename(name: str, max_len: int = 180) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("_. ")
    return name[:max_len] or "sem_nome"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def fetch(url: str, session: requests.Session) -> requests.Response:
    resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def parse_editais(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    editais: list[dict] = []
    seen_pdfs: set[str] = set()

    for span in soup.select("span.paneltitle-value"):
        title = span.get_text(" ", strip=True)
        if not title:
            continue

        title_norm = normalize(title)

        container = None
        h4 = span.find_parent("h4")
        anchor = h4.find_parent("a", href=True) if h4 is not None else None
        if anchor is not None and anchor.get("href", "").startswith("#"):
            target_id = anchor["href"].lstrip("#")
            container = soup.find(id=target_id)

        if container is None:
            container = span.find_parent(class_="panel") or span.find_parent("li") or soup

        pdf_links = [
            a for a in container.find_all("a", href=True)
            if a["href"].lower().split("?")[0].endswith(".pdf")
            or "/media/fapes/editais/" in a["href"].lower()
        ]

        main_pdf = None
        for a in pdf_links:
            link_title = (a.get("title") or "").strip()
            if link_title.lower().startswith("baixar:"):
                link_title_clean = link_title.split(":", 1)[1].strip()
                if normalize(link_title_clean) == title_norm:
                    main_pdf = a["href"]
                    break

        if main_pdf is None and pdf_links:
            main_pdf = pdf_links[0]["href"]

        anexos: list[str] = []
        for a in pdf_links:
            href = a["href"]
            if href != main_pdf and href not in anexos:
                anexos.append(urljoin(BASE_URL, href))

        if main_pdf is None:
            continue

        pdf_url = urljoin(BASE_URL, main_pdf)
        if pdf_url in seen_pdfs:
            continue
        seen_pdfs.add(pdf_url)

        editais.append({
            "titulo": title,
            "pdf_url": pdf_url,
            "anexos": anexos,
        })

    return editais


def download_pdf(url: str, dest: Path, session: requests.Session) -> tuple[bool, str]:
    try:
        with session.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "").lower()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        fh.write(chunk)
            tmp.rename(dest)
            return True, ctype
    except requests.RequestException as exc:
        return False, str(exc)


def filename_from_url(url: str, fallback: str) -> str:
    name = unquote(url.rsplit("/", 1)[-1])
    name = name.split("?")[0]
    if not name.lower().endswith(".pdf"):
        name = sanitize_filename(fallback) + ".pdf"
    else:
        name = sanitize_filename(name.rsplit(".", 1)[0]) + ".pdf"
    return name


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    relatorio: list[dict] = []

    with requests.Session() as session:
        for categoria, url in PAGES:
            print(f"\n=== {categoria} ===")
            print(f"URL: {url}")

            try:
                resp = fetch(url, session)
            except requests.RequestException as exc:
                print(f"  [erro] falha ao acessar a pagina: {exc}")
                relatorio.append({"categoria": categoria, "url": url, "erro": str(exc), "editais": []})
                continue

            editais = parse_editais(resp.text)
            print(f"  Editais abertos encontrados: {len(editais)}")

            cat_dir = OUTPUT_DIR / categoria
            if editais:
                cat_dir.mkdir(parents=True, exist_ok=True)

            for ed in editais:
                titulo = ed["titulo"]
                pdf_url = ed["pdf_url"]
                filename = sanitize_filename(titulo) + ".pdf"
                dest = cat_dir / filename

                if dest.exists() and dest.stat().st_size > 0:
                    print(f"  [pular] ja existe: {filename}")
                    ed["arquivo_local"] = str(dest.relative_to(OUTPUT_DIR))
                    ed["status"] = "ja_existia"
                    continue

                print(f"  [baixar] {titulo}")
                print(f"           {pdf_url}")
                ok, info = download_pdf(pdf_url, dest, session)
                if ok:
                    print(f"           -> {dest.relative_to(OUTPUT_DIR)} ({dest.stat().st_size} bytes)")
                    ed["arquivo_local"] = str(dest.relative_to(OUTPUT_DIR))
                    ed["status"] = "baixado"
                else:
                    print(f"           [erro] {info}")
                    ed["status"] = "erro"
                    ed["erro"] = info
                time.sleep(PAUSE_SECONDS)

            relatorio.append({
                "categoria": categoria,
                "url": url,
                "total": len(editais),
                "editais": editais,
            })

    log_path = OUTPUT_DIR / "_relatorio.json"
    with open(log_path, "w", encoding="utf-8") as fh:
        json.dump(relatorio, fh, ensure_ascii=False, indent=2)

    total = sum(item.get("total", 0) for item in relatorio)
    print(f"\nResumo: {total} edital(is) processado(s) em {len(PAGES)} pagina(s).")
    print(f"Relatorio salvo em: {log_path}")


if __name__ == "__main__":
    main()
