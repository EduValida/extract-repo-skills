# extract-repo-skills

Módulo do **GT-EduValida** para extração de evidências técnicas e mapeamento de competências de contribuidores em repositórios Git (locais ou remotos), vinculando cada habilidade inferida a evidências auditáveis de código.

---

## Visão Geral e Princípios

- **Foco no trabalho real do autor**: A pilha de tecnologias do repositório por si só **não** justifica atribuir uma competência a um contribuidor. Por exemplo, modificar apenas documentação ou arquivos de configuração em um repositório Rust ou Django não demonstra proficiência na linguagem.
- **Rastreabilidade e Evidência Auditável**: Cada habilidade avaliada deve conter citações exatas de linhas modificadas (`changed_lines`), hash do commit e caminho do arquivo.
- **Catálogo de Referência Estruturado**: Avaliações utilizam árvores de competências extraídas de roadmaps consolidados (como o [roadmap.sh](https://roadmap.sh)), sem inventar identificadores ou habilidades arbitrárias.
- **Distinção entre Ausência de Evidência e Lacuna**: O sistema diferencia expressamente quando não há evidência suficiente (`insufficient_evidence`) de uma lacuna no catálogo (`catalog_gap`).

---

## Pré-requisitos e Instalação

- **Python 3.14+** (recomendado gerenciamento com [`uv`])
- **Git**
- **cloc** (opcional para métricas detalhadas de linhas de código)

### Instalação das dependências

```bash
uv sync --extra api --extra llm --extra dev
```

Se desejar suporte a métricas de linhas de código com `cloc`:
```bash
# Instalação rápida do exec no ambiente virtual:
curl -fsSL https://raw.githubusercontent.com/AlDanial/cloc/v2.10/cloc -o .venv/bin/cloc && chmod +x .venv/bin/cloc
```

---

## Configuração (`.env`)

Crie ou edite o arquivo `.env` na raiz do projeto:

```dotenv
# Repositórios locais registrados (formato JSON: "alias": "/caminho/absoluto")
EXTRACT_LOCAL_REPOSITORIES='{"meu-projeto": "/home/usuario/src/meu-projeto"}'

# Hosts remotos permitidos para clonagem HTTPS pública
EXTRACT_REMOTE_HOSTS=github.com,gitlab.com

# Provedor LLM para avaliação semântica
DEEPSEEK_API_KEY=sua-chave-aqui
DEEPSEEK_MODEL=deepseek-chat

# Orçamentos de entrada e contexto
EXTRACT_MAX_INPUT_CHARS=100000
EXTRACT_MAX_CONTEXT_CHARS=25000
```

---

## Executando o Serviço (API)

A interface principal do projeto é a API FastAPI:

```bash
uv run uvicorn extract_repo_skills.api:app --reload --port 8000
```

- **Swagger / Docs interativo**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Healthcheck**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## Como Usar

A análise é acionada através do endpoint `POST /analyses`.

### 1. Modo Preview / Simulação (`dry_run: true`)
Permite inspecionar os artefatos estáticos coletados (diffs, métricas e prompt preparado) **sem** fazer chamadas a modelos de LLM e sem consumir créditos:

```bash
curl -X POST http://127.0.0.1:8000/analyses \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "local:meu-projeto",
    "user": {
      "emails": ["contribuinte@exemplo.com"]
    },
    "skill": "rust",
    "dry_run": true
  }'
```

### 2. Análise Completa com LLM [NAO TESTADO]
Executa a extração estática, monta o contexto focado na árvore da skill informada e solicita ao modelo a proposta de avaliação vinculada às linhas de código:

```bash
curl -X POST http://127.0.0.1:8000/analyses \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "local:meu-projeto",
    "user": {
      "emails": ["contribuinte@exemplo.com"]
    },
    "skill": "rust"
  }'
```

### 3. Repositório Remoto (HTTPS) [NAO POLIDO/EXPERIMENTAL]
É possível analisar diretamente repositórios Git públicos remotos autorizados em `EXTRACT_REMOTE_HOSTS`:

```bash
curl -X POST http://127.0.0.1:8000/analyses \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "https://github.com/exemplo/repositorio.git",
    "user": {
      "emails": ["contribuinte@exemplo.com"]
    },
    "skill": "python"
  }'
```

---

## Catálogo e Grafos de Habilidades

Os grafos extraídos ficam em `catalogs/graphs/`. Os roadmaps enxutos ficam em
`catalogs/roadmaps/` e são gerados a partir da árvore dos grafos.

Para baixar ou atualizar os 95 roadmaps oficiais diretamente do roadmap.sh em
formato de grafo estruturado:

```bash
uv run python scripts/extract_roadmap_graph.py --all
```

Para regenerar os roadmaps enxutos:

```bash
uv run python scripts/build_roadmaps.py
```

O schema, os campos e as regras de geração estão em
[`docs/roadmap-schema.md`](docs/roadmap-schema.md).

O campo `skill` recebe o título do roadmap. Por exemplo, `"Network Engineer"`
carrega `catalogs/roadmaps/network-engineer.json`. Slugs antigos continuam
aceitos como fallback.

---

## Testes

Para executar toda a suíte de testes unitários:

```bash
uv run python -m unittest discover tests
```
