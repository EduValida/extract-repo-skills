# Schema enxuto de roadmaps

Os roadmaps ficam em `catalogs/roadmaps/` e são gerados a partir de
`catalogs/graphs/`. O schema remove os metadados da extração e mantém apenas a
árvore e os pesos.

## Estrutura

```json
{
  "title": "Network Engineer",
  "topics": [
    {
      "title": "Introduction",
      "importance": 50,
      "completion_threshold": 60,
      "children": [
        {
          "title": "Basic Terminology",
          "children": [
            { "title": "Client" },
            { "title": "Server" }
          ]
        }
      ]
    },
    {
      "title": "Routing",
      "importance": 50,
      "completion_threshold": 60,
      "children": [
        { "title": "OSPF" },
        { "title": "BGP" }
      ]
    }
  ]
}
```

## Campos

### Raiz

| Campo | Obrigatório | Regra |
| --- | --- | --- |
| `title` | Sim | Título do roadmap |
| `topics` | Sim | Lista não vazia de tópicos |

### Nó

| Campo | Obrigatório | Regra |
| --- | --- | --- |
| `title` | Sim | Nome do tópico ou item |
| `children` | Não | Ausente indica folha; presente indica grupo |
| `importance` | Não | Inteiro de 0 a 100 |
| `completion_threshold` | Não | Inteiro de 0 a 100 |

Folhas não têm `children`. Grupos podem ter `importance` e
`completion_threshold`; o gerador aplica os defaults `50` e `60`.

Títulos de irmãos devem ser únicos. Quando o grafo original repete um título, o
gerador acrescenta um sufixo, por exemplo `Client (2)`.

## Geração

```bash
uv run python scripts/build_roadmaps.py
```

O comando lê todos os arquivos de `catalogs/graphs/`, exceto `index.json`, e
escreve um arquivo em `catalogs/roadmaps/` com o mesmo nome.

Para sobrescrever roadmaps existentes:

```bash
uv run python scripts/build_roadmaps.py --force
```

É possível trocar os defaults:

```bash
uv run python scripts/build_roadmaps.py --importance 70 --completion-threshold 65
```

## Carregamento

`load_roadmap()` aceita:

- título exato: `"Network Engineer"`;
- título sem diferenciar maiúsculas/minúsculas: `"network engineer"`;
- slug do arquivo: `"network-engineer"`.

## O que não entra no schema

- `metadata`;
- `tree`;
- `graph`;
- `id`;
- `description`;
- `resources`;
- `file_path`;
- `type`;
- `subtopics_count`;
- `criteria`;
- `schema_version`;
- `version`;
- `review_status`.

## Sem critérios

O título do item é o critério implícito. A classificação deve seguir regras
globais do prompt:

- `presente`: a contribuição demonstra o comportamento nomeado pelo título;
- `parcial`: há evidência relacionada, mas não do item específico;
- `transferivel`: há fundamento aplicável, sem demonstração direta;
- `ausente`: não foi encontrado no recorte analisado.

Menções, dependências, arquivos não alterados e edição apenas de formatação não
são evidência.

Se for necessário detalhar um item, `criteria` pode ser adicionado como campo
opcional em uma evolução futura. Nesta versão, o título é a única definição
por item.
