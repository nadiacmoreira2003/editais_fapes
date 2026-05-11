# Monitor de Editais da FAPES

Automação que **baixa diariamente os editais abertos da FAPES**
([fapes.es.gov.br](https://fapes.es.gov.br)), **extrai os metadados com a IA do
Gemini** (objetivo, público-alvo, valores, cronograma, contato) e **envia um
e-mail automático** sempre que aparece um edital novo ou quando um edital já
publicado sofre alterações relevantes.

Tudo roda de graça no GitHub Actions, todo dia às 08:00 (horário de Brasília).

Repositório: <https://github.com/nadiacmoreira2003/editais_fapes>

---

## O que o projeto faz

Um pipeline em três etapas:

| Etapa | Script | O que faz |
|-------|--------|-----------|
| 1 | `baixar_editais_fapes.py` | Faz scraping das 6 páginas de editais abertos da FAPES e baixa os PDFs (edital + alterações/retificações) para `editais_fapes/<categoria>/`. |
| 2 | `extrair_editais_gemini.py` | Manda cada PDF (e suas alterações) para o Gemini e extrai um JSON estruturado com objetivo, valores, contato e cronograma. Tem cache por SHA-256: só re-extrai quando o PDF muda. |
| 3 | `verificar_alteracoes.py` | Compara o resultado de hoje com o estado da execução anterior, envia e-mail dos editais novos ou atualizados e gera a planilha `_extracao.xlsx`. |

Categorias monitoradas: Formação Científica, Pesquisa, Difusão do Conhecimento,
Extensão, Inovação e Chamadas Internacionais.

---

## Pré-requisitos

Você vai precisar de:

1. **Python 3.10 ou superior** ([python.org/downloads](https://www.python.org/downloads/))
2. **Git** ([git-scm.com/downloads](https://git-scm.com/downloads))
3. **Conta no Google AI Studio** (gratuita) para a chave do Gemini
4. **Conta Gmail com verificação em 2 etapas** (gratuita) para enviar os e-mails
5. **Conta no GitHub** (gratuita) — só se você quiser deixar rodando automático

---

## Passo 1 — Clonar o repositório

```bash
git clone https://github.com/nadiacmoreira2003/editais_fapes.git
cd editais_fapes
```

## Passo 2 — Criar um ambiente virtual e instalar as dependências

No macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Passo 3 — Pegar a chave da API do Gemini

1. Acesse <https://aistudio.google.com/apikey>
2. Faça login com sua conta Google
3. Clique em **Create API Key** e copie a chave gerada

A cota gratuita do `gemini-2.5-flash` é suficiente para rodar o projeto uma vez
por dia, com folga.

## Passo 4 — Configurar o e-mail (Gmail App Password)

O script envia e-mails pelo SMTP do Gmail. Como o Google não aceita mais a senha
normal da conta para SMTP, você precisa gerar uma **App Password** (senha de
aplicativo) de 16 caracteres:

1. Ative a verificação em 2 etapas em <https://myaccount.google.com/security>
   (sem isso, o Google não mostra a opção de App Password).
2. Acesse <https://myaccount.google.com/apppasswords>
3. Em "Nome do app", escreva qualquer coisa (ex.: `Editais FAPES`) e clique em
   **Criar**.
4. Copie a senha de 16 letras que aparece (sem espaços).

## Passo 5 — Criar o arquivo `.env`

Copie o `.env.example` para `.env` e preencha os campos:

```bash
cp .env.example .env
```

Abra o `.env` em um editor de texto e preencha:

```env
GEMINI_API_KEY=cole_aqui_a_chave_do_passo_3

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=seu_email@gmail.com
SMTP_PASSWORD=cole_aqui_a_app_password_do_passo_4
EMAIL_TO=email_que_vai_receber_os_avisos@exemplo.com
```

> O arquivo `.env` está no `.gitignore` e nunca é enviado ao GitHub.
> O `EMAIL_TO` pode ser igual ao `SMTP_USER` se você quer mandar para si mesmo.

## Passo 6 — Rodar o pipeline manualmente

Sempre na mesma ordem:

```bash
python baixar_editais_fapes.py        # 1) baixa os PDFs
python extrair_editais_gemini.py      # 2) extrai os metadados via Gemini
python verificar_alteracoes.py        # 3) detecta mudanças e envia e-mails
```

Na primeira execução, **todos os editais abertos vão chegar como "novos"** no
seu e-mail. Nas execuções seguintes, só chegam novidades.

---

## O que aparece na pasta `editais_fapes/`

Depois de rodar, você terá:

```
editais_fapes/
├── formacao_cientifica/
│   ├── Edital_FAPES_n_01-2026_....pdf
│   ├── Edital_FAPES_n_01-2026_....json     ← extração do Gemini
│   └── _alteracoes/                        ← retificações/erratas
├── pesquisa/
├── ...
├── _relatorio.json     ← lista bruta do que foi baixado
├── _extracao.json      ← JSON consolidado de tudo que o Gemini extraiu
├── _state.json         ← estado entre execuções (para detectar mudanças)
└── _extracao.xlsx      ← planilha Excel com tudo formatado
```

A planilha `_extracao.xlsx` é o entregável principal: tem categoria, edital,
status (novo / atualizado / sem alterações), próxima ação do proponente, datas
de submissão, valores, contato e link do PDF.

---

## Passo 7 (opcional) — Deixar rodando automático no GitHub Actions

Se você fez fork do repositório (ou clonou para um repositório seu), o workflow
em `.github/workflows/check-editais.yml` já está pronto para rodar todo dia às
**08:00 BRT**. Falta só cadastrar os segredos:

1. No GitHub, abra seu repositório → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Crie estes 4 segredos, um de cada vez (mesmos valores do `.env`):

   | Nome | Conteúdo |
   |------|----------|
   | `GEMINI_API_KEY` | sua chave do Gemini |
   | `SMTP_USER` | seu e-mail Gmail |
   | `SMTP_PASSWORD` | sua App Password de 16 caracteres |
   | `EMAIL_TO` | e-mail que vai receber os avisos |

3. Vá em **Actions** → **Verificar editais FAPES** → **Run workflow** para
   testar manualmente. Se passar, daqui para frente o GitHub roda sozinho todo
   dia, faz commit dos arquivos atualizados e envia os e-mails.

> O workflow precisa de permissão de escrita no repositório para conseguir
> commitar o `_state.json` e os PDFs novos. Em **Settings → Actions → General →
> Workflow permissions**, marque **Read and write permissions**.

---

## Como o monitoramento de mudanças funciona

A cada execução, o script compara o edital atual com o estado salvo em
`_state.json` e dispara e-mail quando detecta:

- **Edital novo**: nunca foi visto antes → e-mail verde "Novo edital".
- **Edital atualizado** (depois que o primeiro e-mail já saiu) — qualquer um destes:
  - Mudou objetivo, público-alvo, valor total, valor por proposta ou contato.
  - Foi adicionada/removida uma data de submissão de propostas.
  - Foi publicada uma nova retificação/errata.
- **Edital removido**: marcado na planilha; não dispara e-mail.

Editais que não mudaram nada **não geram e-mail repetido** — você só recebe
notificação quando vale a pena olhar.

---

## Estrutura do repositório

```
.
├── baixar_editais_fapes.py        # scraping dos PDFs
├── extrair_editais_gemini.py      # extração via Gemini
├── verificar_alteracoes.py        # diff + e-mail + xlsx
├── requirements.txt               # dependências Python
├── .env.example                   # modelo do .env
├── .github/workflows/
│   └── check-editais.yml          # automação diária
└── editais_fapes/                 # gerado em runtime
```

---

## Problemas comuns

**`Defina GEMINI_API_KEY no arquivo .env`**
Falta criar o `.env` ou a variável está vazia. Volte ao Passo 5.

**`smtplib.SMTPAuthenticationError`**
Você usou a senha normal do Gmail. Tem que ser **App Password** (Passo 4) e a
verificação em 2 etapas precisa estar ativa.

**`[stop] Cota diaria do Gemini esgotada`**
A cota gratuita do Gemini foi atingida. O script já salvou o que conseguiu
processar; rode de novo no dia seguinte e ele continua de onde parou (graças ao
cache por SHA-256).

**O GitHub Actions falha em `git push`**
Falta dar permissão de escrita: **Settings → Actions → General → Workflow
permissions → Read and write permissions**.

**O e-mail não chega**
Cheque o spam. No log do script aparece `[email novo] ...` quando o envio é
bem-sucedido — se não aparece, o problema é nas variáveis SMTP.

---

## Licença

Projeto pessoal de monitoramento, sem licença específica. Use à vontade.
# editais_fapes
