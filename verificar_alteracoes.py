"""Compara a extracao atual com o estado salvo da execucao anterior, detecta:
  - editais novos (nao existiam antes)
  - editais atualizados (PDF republicado E algum campo nosso mudou)
  - editais removidos (nao estao mais abertos na FAPES)

Para cada edital novo ou atualizado, envia um e-mail e marca a coluna
"Status" na planilha indicando que o aviso ja foi enviado.

Le as credenciais SMTP do .env (local) ou variaveis de ambiente (GitHub Actions).
Atualiza editais_fapes/_state.json e gera editais_fapes/_extracao.xlsx.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from extrair_editais_gemini import (
    CATEGORIA_DISPLAY,
    acoes_resumidas,
    formatar_evento,
    proxima_acao,
    somente_acoes,
)

ROOT = Path(__file__).resolve().parent
EDITAIS_DIR = ROOT / "editais_fapes"
EXTRACAO_JSON = EDITAIS_DIR / "_extracao.json"
STATE_PATH = EDITAIS_DIR / "_state.json"
EXTRACAO_XLSX = EDITAIS_DIR / "_extracao.xlsx"

TZ = ZoneInfo("America/Sao_Paulo")

CAMPOS_MONITORADOS = [
    "objetivo",
    "publico_alvo",
    "valor_total",
    "valor_por_proposta",
    "contato",
    "observacoes_gerais",
]


def agora_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def chave(reg: dict[str, Any]) -> str:
    return f"{reg['categoria']}||{reg['titulo']}"


def crono_para_set(cronograma: list[dict[str, Any]]) -> set[tuple]:
    items = set()
    for ev in cronograma or []:
        items.add((
            ev.get("evento", ""),
            ev.get("data_inicio"),
            ev.get("data_fim"),
            bool(ev.get("acao_do_proponente")),
        ))
    return items


def diff_extracao(antigo: dict[str, Any], novo: dict[str, Any]) -> list[str]:
    mudancas: list[str] = []
    for campo in CAMPOS_MONITORADOS:
        v_antigo = antigo.get(campo)
        v_novo = novo.get(campo)
        if v_antigo != v_novo:
            mudancas.append(f"{campo}: {v_antigo!r} -> {v_novo!r}")

    set_antigo = crono_para_set(antigo.get("cronograma") or [])
    set_novo = crono_para_set(novo.get("cronograma") or [])
    adicionados = set_novo - set_antigo
    removidos = set_antigo - set_novo
    for ev in sorted(adicionados):
        mudancas.append(f"cronograma + {ev[0]} ({ev[1] or '?'} a {ev[2] or '?'})")
    for ev in sorted(removidos):
        mudancas.append(f"cronograma - {ev[0]} ({ev[1] or '?'} a {ev[2] or '?'})")
    return mudancas


def status_label(state_entry: dict[str, Any]) -> str:
    s = state_entry.get("status_atual", "")
    when = state_entry.get("email_enviado_em")
    if when:
        try:
            dt = datetime.fromisoformat(when).strftime("%d/%m/%Y")
        except Exception:
            dt = when[:10]
    else:
        dt = None

    if s == "novo":
        return f"Novo - e-mail enviado em {dt}" if dt else "Novo - aguardando envio"
    if s == "atualizado":
        return f"Atualizado - e-mail enviado em {dt}" if dt else "Atualizado - aguardando envio"
    if s == "removido":
        return "Removido (nao mais aberto)"
    return "Sem alteracoes"


def carregar_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {"editais": {}, "ultima_execucao": None}


def salvar_state(state: dict[str, Any]) -> None:
    state["ultima_execucao"] = agora_iso()
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def montar_email_novo(reg: dict[str, Any]) -> tuple[str, str, str]:
    cat = CATEGORIA_DISPLAY.get(reg["categoria"], reg["categoria"])
    titulo = reg["titulo"]
    ext = reg.get("extracao") or {}
    cron = ext.get("cronograma") or []

    subject = f"[FAPES] Novo edital: {titulo}"

    plain = f"""Categoria: {cat}
Edital: {titulo}

OBJETIVO
{ext.get('objetivo') or '-'}

PUBLICO-ALVO: {ext.get('publico_alvo') or '-'}
VALOR TOTAL: {ext.get('valor_total') or '-'}
VALOR POR PROPOSTA: {ext.get('valor_por_proposta') or '-'}
CONTATO: {ext.get('contato') or '-'}

PROXIMA ACAO DO PROPONENTE
{proxima_acao(cron) or '(nenhuma data identificada)'}

ACOES DO PROPONENTE
{acoes_resumidas(cron) or '(sem cronograma com data)'}

PDF: {reg.get('pdf_url', '')}
"""

    rows = "".join(
        f"<li>{formatar_evento(ev)}</li>" for ev in somente_acoes(cron)
    ) or "<li><em>(sem cronograma com data)</em></li>"

    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:680px">
<h2 style="color:#1a4d7a">Novo edital FAPES</h2>
<p><strong>Categoria:</strong> {cat}<br>
<strong>Edital:</strong> {titulo}</p>

<h3>Objetivo</h3>
<p>{(ext.get('objetivo') or '-').replace(chr(10), '<br>')}</p>

<table style="border-collapse:collapse">
<tr><td><strong>Publico-alvo:</strong></td><td>{ext.get('publico_alvo') or '-'}</td></tr>
<tr><td><strong>Valor total:</strong></td><td>{ext.get('valor_total') or '-'}</td></tr>
<tr><td><strong>Valor por proposta:</strong></td><td>{ext.get('valor_por_proposta') or '-'}</td></tr>
<tr><td><strong>Contato:</strong></td><td>{ext.get('contato') or '-'}</td></tr>
</table>

<h3>Proxima acao do proponente</h3>
<p>{proxima_acao(cron) or '<em>(nenhuma data identificada)</em>'}</p>

<h3>Acoes do proponente (cronograma filtrado)</h3>
<ul>{rows}</ul>

<p><a href="{reg.get('pdf_url', '')}">Baixar PDF do edital</a></p>
</body></html>"""

    return subject, plain, html


def montar_email_atualizado(reg: dict[str, Any], mudancas: list[str]) -> tuple[str, str, str]:
    cat = CATEGORIA_DISPLAY.get(reg["categoria"], reg["categoria"])
    titulo = reg["titulo"]
    ext = reg.get("extracao") or {}

    subject = f"[FAPES] Edital atualizado: {titulo}"

    plain_changes = "\n".join(f"  - {m}" for m in mudancas) or "  (sem alteracoes detectadas)"
    plain = f"""Categoria: {cat}
Edital: {titulo}

ALTERACOES DETECTADAS
{plain_changes}

ESTADO ATUAL

OBJETIVO
{ext.get('objetivo') or '-'}

PUBLICO-ALVO: {ext.get('publico_alvo') or '-'}
VALOR TOTAL: {ext.get('valor_total') or '-'}
VALOR POR PROPOSTA: {ext.get('valor_por_proposta') or '-'}
CONTATO: {ext.get('contato') or '-'}

PROXIMA ACAO DO PROPONENTE
{proxima_acao(ext.get('cronograma') or []) or '(nenhuma data identificada)'}

PDF: {reg.get('pdf_url', '')}
"""

    html_changes = "".join(f"<li>{m}</li>" for m in mudancas) or "<li><em>(sem alteracoes detectadas)</em></li>"
    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:680px">
<h2 style="color:#a0522d">Edital FAPES atualizado</h2>
<p><strong>Categoria:</strong> {cat}<br>
<strong>Edital:</strong> {titulo}</p>

<h3>Alteracoes detectadas</h3>
<ul>{html_changes}</ul>

<h3>Objetivo (atual)</h3>
<p>{(ext.get('objetivo') or '-').replace(chr(10), '<br>')}</p>

<table style="border-collapse:collapse">
<tr><td><strong>Valor total:</strong></td><td>{ext.get('valor_total') or '-'}</td></tr>
<tr><td><strong>Valor por proposta:</strong></td><td>{ext.get('valor_por_proposta') or '-'}</td></tr>
<tr><td><strong>Proxima acao:</strong></td><td>{proxima_acao(ext.get('cronograma') or []) or '-'}</td></tr>
</table>

<p><a href="{reg.get('pdf_url', '')}">Baixar PDF do edital</a></p>
</body></html>"""

    return subject, plain, html


def enviar_email(subject: str, plain: str, html: str, smtp_cfg: dict[str, str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["from"]
    msg["To"] = smtp_cfg["to"]
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(smtp_cfg["host"], int(smtp_cfg["port"])) as srv:
        srv.starttls()
        srv.login(smtp_cfg["user"], smtp_cfg["password"])
        srv.send_message(msg)


def carregar_smtp() -> dict[str, str] | None:
    user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASSWORD")
    to = os.getenv("EMAIL_TO")
    if not (user and pw and to):
        return None
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": os.getenv("SMTP_PORT", "587"),
        "user": user,
        "password": pw,
        "from": user,
        "to": to,
    }


def gerar_xlsx(consolidados: list[dict[str, Any]], state: dict[str, Any]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Editais"
    headers = [
        "Categoria",
        "Edital",
        "Status",
        "Próxima ação do proponente",
        "Alterações detectadas",
        "Objetivo",
        "Público-alvo",
        "Valor total",
        "Valor por proposta",
        "Contato",
        "Ações do proponente (todas)",
        "Observações",
        "PDF (URL)",
        "Arquivo local",
    ]
    ws.append(headers)

    fill_novo = PatternFill("solid", fgColor="C6EFCE")
    fill_atualizado = PatternFill("solid", fgColor="FFEB9C")

    for reg in consolidados:
        ext = reg.get("extracao") or {}
        cron = ext.get("cronograma") or []
        ent = state["editais"].get(chave(reg), {})
        status_txt = status_label(ent)
        alteracoes = "\n".join(ent.get("alteracoes_recentes") or [])
        ws.append([
            CATEGORIA_DISPLAY.get(reg.get("categoria", ""), reg.get("categoria", "")),
            reg.get("titulo", ""),
            status_txt,
            proxima_acao(cron),
            alteracoes,
            ext.get("objetivo", ""),
            ext.get("publico_alvo", ""),
            ext.get("valor_total", ""),
            ext.get("valor_por_proposta", ""),
            ext.get("contato", ""),
            acoes_resumidas(cron),
            ext.get("observacoes_gerais", ""),
            reg.get("pdf_url", ""),
            reg.get("arquivo_local", ""),
        ])
        if status_txt.startswith("Novo"):
            ws.cell(row=ws.max_row, column=3).fill = fill_novo
        elif status_txt.startswith("Atualizado"):
            ws.cell(row=ws.max_row, column=3).fill = fill_atualizado

    widths = {"A": 22, "B": 55, "C": 38, "D": 50, "E": 45,
              "F": 60, "G": 35, "H": 18, "I": 18, "J": 35,
              "K": 60, "L": 40, "M": 55, "N": 45}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    bold = Font(bold=True)
    head_align = Alignment(wrap_text=True, vertical="center", horizontal="center")
    body_align = Alignment(wrap_text=True, vertical="top")
    for cell in ws[1]:
        cell.font = bold
        cell.alignment = head_align
    ws.row_dimensions[1].height = 32
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = body_align
    ws.freeze_panes = "C2"
    wb.save(EXTRACAO_XLSX)


def main() -> None:
    load_dotenv(ROOT / ".env")

    if not EXTRACAO_JSON.exists():
        sys.exit(f"Nao encontrei {EXTRACAO_JSON}. Rode antes: python3 extrair_editais_gemini.py")

    with open(EXTRACAO_JSON, encoding="utf-8") as fh:
        consolidados = json.load(fh)

    state = carregar_state()
    smtp = carregar_smtp()
    if smtp is None:
        print("[aviso] Variaveis SMTP_* / EMAIL_TO ausentes - e-mails NAO serao enviados.")

    chaves_atuais = set()
    reg_por_chave: dict[str, dict] = {}

    for reg in consolidados:
        k = chave(reg)
        chaves_atuais.add(k)
        reg_por_chave[k] = reg
        ext_novo = reg.get("extracao") or {}
        ent = state["editais"].get(k)

        if ent is None:
            state["editais"][k] = {
                "categoria": reg["categoria"],
                "titulo": reg["titulo"],
                "pdf_url": reg.get("pdf_url"),
                "pdf_sha256": reg.get("pdf_sha256"),
                "primeira_visualizacao": agora_iso(),
                "ultima_visualizacao": agora_iso(),
                "status_atual": "novo",
                "email_enviado_em": None,
                "ultima_alteracao_em": agora_iso(),
                "alteracoes_recentes": [],
                "extracao": ext_novo,
            }
            continue

        ent["ultima_visualizacao"] = agora_iso()
        ent["pdf_url"] = reg.get("pdf_url")
        hash_anterior = ent.get("pdf_sha256")
        ent["pdf_sha256"] = reg.get("pdf_sha256")

        if hash_anterior == reg.get("pdf_sha256"):
            if ent.get("status_atual") in ("novo", "atualizado") and ent.get("email_enviado_em"):
                ent["status_atual"] = "sem_alteracoes"
                ent["alteracoes_recentes"] = []
            ent["extracao"] = ext_novo
            continue

        mudancas = diff_extracao(ent.get("extracao") or {}, ext_novo)
        ent["extracao"] = ext_novo
        if mudancas:
            ent["status_atual"] = "atualizado"
            ent["email_enviado_em"] = None
            ent["alteracoes_recentes"] = mudancas
            ent["ultima_alteracao_em"] = agora_iso()
        elif ent.get("status_atual") == "novo" and ent.get("email_enviado_em"):
            ent["status_atual"] = "sem_alteracoes"

    for k, ent in state["editais"].items():
        if k not in chaves_atuais and ent.get("status_atual") != "removido":
            ent["status_atual"] = "removido"
            ent["alteracoes_recentes"] = []

    pendentes_novos: list[tuple[dict, str]] = []
    pendentes_atualizados: list[tuple[dict, str, list[str]]] = []
    for k in chaves_atuais:
        ent = state["editais"][k]
        if ent.get("email_enviado_em"):
            continue
        reg = reg_por_chave[k]
        if ent.get("status_atual") == "novo":
            pendentes_novos.append((reg, k))
        elif ent.get("status_atual") == "atualizado":
            pendentes_atualizados.append((reg, k, ent.get("alteracoes_recentes") or []))

    print(f"\n  Novos pendentes:       {len(pendentes_novos)}")
    print(f"  Atualizados pendentes: {len(pendentes_atualizados)}")
    print(f"  Total atual:           {len(consolidados)}")

    if smtp:
        for reg, k in pendentes_novos:
            subject, plain, html = montar_email_novo(reg)
            try:
                enviar_email(subject, plain, html, smtp)
                state["editais"][k]["email_enviado_em"] = agora_iso()
                print(f"  [email novo] {reg['titulo'][:60]}")
            except Exception as exc:
                print(f"  [erro email novo] {reg['titulo'][:60]}: {exc}")

        for reg, k, mudancas in pendentes_atualizados:
            subject, plain, html = montar_email_atualizado(reg, mudancas)
            try:
                enviar_email(subject, plain, html, smtp)
                state["editais"][k]["email_enviado_em"] = agora_iso()
                print(f"  [email atualizado] {reg['titulo'][:60]}")
            except Exception as exc:
                print(f"  [erro email atual] {reg['titulo'][:60]}: {exc}")

    salvar_state(state)
    gerar_xlsx(consolidados, state)
    print(f"\nState atualizado: {STATE_PATH}")
    print(f"Planilha:         {EXTRACAO_XLSX}")


if __name__ == "__main__":
    main()
