"""Le os PDFs baixados pelo baixar_editais_fapes.py e usa o Gemini
para extrair objetivo, cronograma e demais metadados de cada edital.

Salva, para cada edital:
  - <nome_do_pdf>.json (ao lado do PDF, na pasta da categoria)
e consolida tudo em:
  - editais_fapes/_extracao.json
  - editais_fapes/_extracao.xlsx
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent
EDITAIS_DIR = ROOT / "editais_fapes"
RELATORIO_PATH = EDITAIS_DIR / "_relatorio.json"
EXTRACAO_JSON = EDITAIS_DIR / "_extracao.json"

MODEL_NAME = "gemini-2.5-flash"
SCHEMA_VERSION = 2

CATEGORIA_DISPLAY = {
    "formacao_cientifica":     "Formação Científica",
    "pesquisa":                "Pesquisa",
    "difusao_do_conhecimento": "Difusão do Conhecimento",
    "extensao":                "Extensão",
    "inovacao":                "Inovação",
    "chamadas_internacionais": "Chamadas Internacionais",
}

PROMPT = """Voce esta analisando um edital de fomento a pesquisa publicado pela FAPES (Fundacao de Amparo a Pesquisa e Inovacao do Espirito Santo).

Extraia as informacoes do PDF e responda APENAS com um JSON valido, sem texto adicional, no seguinte formato:

{
  "objetivo": "Descricao do objetivo do edital em 2 a 5 frases, em portugues, destacando o que sera financiado e a quem se destina.",
  "publico_alvo": "Quem pode submeter propostas (ex.: pesquisadores doutores vinculados a ICTs do ES). Se nao especificado, use null.",
  "valor_total": "Valor total do edital com moeda (ex.: 'R$ 1.500.000,00'). Se nao especificado, use null.",
  "valor_por_proposta": "Valor maximo por proposta com moeda. Se nao especificado, use null.",
  "contato": "E-mail de contato para duvidas. Se houver mais de um, separe por virgula. Se nao especificado, use null.",
  "cronograma": [
    {
      "evento": "Nome da etapa (ex.: 'Lancamento do edital', 'Submissao de propostas', 'Divulgacao do resultado preliminar', 'Interposicao de recursos', 'Resultado final', 'Inicio da execucao').",
      "data_inicio": "AAAA-MM-DD ou null se nao houver",
      "data_fim": "AAAA-MM-DD ou null se for evento pontual ou se nao houver",
      "observacao": "Detalhes uteis (ex.: 'ate as 18h', 'via SIGFAPES', escopo da chamada), ou null",
      "acao_do_proponente": true
    }
  ],
  "observacoes_gerais": "Qualquer informacao relevante que nao se encaixe nos campos acima, ou null."
}

Regras importantes:
- Datas SEMPRE no formato AAAA-MM-DD. Se o edital indicar 'ate 30/04/2026', use data_fim='2026-04-30' e data_inicio=null.
- Se um intervalo de datas for dado (ex.: 'de 10/05/2026 a 20/05/2026'), preencha data_inicio e data_fim.
- O cronograma deve estar em ordem cronologica.
- Inclua TODAS as etapas do cronograma do edital, mesmo as menos obvias.

Como classificar "acao_do_proponente":
- Use TRUE quando o evento exige uma acao do candidato/proponente, ou seja, quando ele precisa enviar/submeter/entregar algo, recorrer ou assinar contrato. Exemplos:
    * Periodo de submissao de propostas (qualquer chamada)
    * Prazo para impugnacao do edital
    * Prazo para interposicao de recursos
    * Prazo para complementacao/envio de documentos
    * Prazo para contratacao / assinatura do termo de outorga
    * Periodo para indicacao de bolsista
- Use FALSE quando o evento e uma acao da FAPES ou apenas informativo. Exemplos:
    * Publicacao/lancamento do edital
    * Analise de habilitacao ou de merito
    * Divulgacao do resultado preliminar ou final
    * Homologacao
    * Inicio da execucao do projeto
    * Vigencia do projeto

- Quando o evento for um "Periodo de submissao" associado a uma sub-chamada (1a, 2a, 3a chamada), inclua na "observacao" o escopo daquela chamada (ex.: "eventos com inicio entre 01/05/2026 a 31/07/2026").
- Responda apenas com o JSON, sem markdown, sem ```json, sem comentarios.
"""


def load_relatorio() -> list[dict[str, Any]]:
    if not RELATORIO_PATH.exists():
        raise FileNotFoundError(
            f"Nao encontrei {RELATORIO_PATH}. Rode antes: python3 baixar_editais_fapes.py"
        )
    with open(RELATORIO_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)


class DailyQuotaExceeded(Exception):
    pass


def _retry_delay_seconds(exc: Exception) -> float | None:
    msg = str(exc)
    m = re.search(r"retry in ([\d.]+)s", msg)
    if m:
        return float(m.group(1))
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", msg)
    if m:
        return float(m.group(1))
    return None


def _is_daily_quota(exc: Exception) -> bool:
    msg = str(exc)
    return "PerDay" in msg or "GenerateRequestsPerDay" in msg


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_one(client: genai.Client, pdf_path: Path,
                max_minute_retries: int = 3) -> dict[str, Any]:
    pdf_bytes = pdf_path.read_bytes()
    parts = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        PROMPT,
    ]
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1,
    )

    attempt = 0
    while True:
        try:
            response = client.models.generate_content(
                model=MODEL_NAME, contents=parts, config=cfg,
            )
            return parse_json_response(response.text)
        except Exception as exc:
            if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                raise
            if _is_daily_quota(exc):
                raise DailyQuotaExceeded(str(exc)) from exc
            attempt += 1
            if attempt > max_minute_retries:
                raise
            wait = _retry_delay_seconds(exc) or 30.0
            wait = min(wait + 1.0, 65.0)
            print(f"  [429] aguardando {wait:.0f}s e tentando novamente "
                  f"(tentativa {attempt}/{max_minute_retries})")
            time.sleep(wait)


def somente_acoes(cronograma: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ev for ev in (cronograma or []) if ev.get("acao_do_proponente")]


def formatar_evento(ev: dict[str, Any]) -> str:
    data_ini = ev.get("data_inicio") or ""
    data_fim = ev.get("data_fim") or ""
    if data_ini and data_fim and data_ini != data_fim:
        data = f"{data_ini} a {data_fim}"
    else:
        data = data_fim or data_ini or "?"
    obs = ev.get("observacao")
    nome = ev.get("evento", "")
    if obs:
        return f"{data}: {nome} ({obs})"
    return f"{data}: {nome}"


def proxima_acao(cronograma: list[dict[str, Any]]) -> str:
    today = time.strftime("%Y-%m-%d")
    futuras = []
    for ev in somente_acoes(cronograma):
        data = ev.get("data_fim") or ev.get("data_inicio")
        if data and data >= today:
            futuras.append((data, ev))
    if not futuras:
        return ""
    futuras.sort(key=lambda t: t[0])
    return formatar_evento(futuras[0][1])


def acoes_resumidas(cronograma: list[dict[str, Any]]) -> str:
    return "\n".join(formatar_evento(ev) for ev in somente_acoes(cronograma))


def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key or api_key.startswith("COLE_"):
        raise SystemExit(
            "Defina GEMINI_API_KEY no arquivo .env (Scraping Fapes/.env)."
        )

    client = genai.Client(api_key=api_key)
    relatorio = load_relatorio()

    consolidados: list[dict[str, Any]] = []
    parar = False

    for cat_block in relatorio:
        if parar:
            break
        categoria = cat_block.get("categoria", "")
        for ed in cat_block.get("editais", []):
            arquivo_rel = ed.get("arquivo_local")
            titulo = ed.get("titulo", "")
            if not arquivo_rel:
                print(f"[pular] sem arquivo local: {titulo}")
                continue

            pdf_path = EDITAIS_DIR / arquivo_rel
            if not pdf_path.exists():
                print(f"[pular] PDF nao encontrado: {pdf_path}")
                continue

            hash_atual = ed.get("pdf_sha256") or sha256_of(pdf_path)

            json_path = pdf_path.with_suffix(".json")
            cache_valido = False
            extracao = None
            if json_path.exists() and json_path.stat().st_size > 0:
                with open(json_path, encoding="utf-8") as fh:
                    extracao = json.load(fh)
                tem_schema = any(
                    "acao_do_proponente" in ev
                    for ev in (extracao.get("cronograma") or [])
                )
                hash_bate = extracao.get("_pdf_sha256") == hash_atual
                cache_valido = tem_schema and hash_bate
                if cache_valido:
                    print(f"[cache] {categoria}/{titulo[:60]}")
                elif tem_schema and not hash_bate:
                    print(f"[hash!] {categoria}/{titulo[:60]} - PDF mudou, re-extraindo")

            if not cache_valido:
                print(f"[gemini] {categoria}/{titulo[:60]}")
                try:
                    extracao = extract_one(client, pdf_path)
                except DailyQuotaExceeded as exc:
                    print(f"  [stop] Cota diaria do Gemini esgotada. Tente novamente amanha.")
                    print(f"         {exc}")
                    parar = True
                    break
                except Exception as exc:
                    print(f"  [erro] {exc}")
                    continue

                extracao["_pdf_sha256"] = hash_atual
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(extracao, fh, ensure_ascii=False, indent=2)
                time.sleep(2.0)

            if extracao is None or "erro" in extracao:
                continue

            consolidados.append({
                "categoria": categoria,
                "titulo": titulo,
                "pdf_url": ed.get("pdf_url", ""),
                "arquivo_local": arquivo_rel,
                "pdf_sha256": hash_atual,
                "extracao": extracao,
            })

    with open(EXTRACAO_JSON, "w", encoding="utf-8") as fh:
        json.dump(consolidados, fh, ensure_ascii=False, indent=2)

    print(f"\nProcessados: {len(consolidados)} edital(is).")
    print(f"JSON consolidado: {EXTRACAO_JSON}")


if __name__ == "__main__":
    main()
