# Proposta: análise por repositório e usuário via FastAPI

Status: fase 1 implementada; fase 2 planejada. Contratos e execução: `docs/spec.md` e `README.md`.
Branch: `feat/repository-user-artifacts-api`.

## Objetivo

Receber um repositório Git e um usuário, coletar suas contribuições em um
recorte explícito, gerar cinco artefatos estáticos e fornecer esse pacote à LLM.
O resultado continua limitado ao catálogo versionado e a evidências verificáveis.
Nenhum código, teste, instalação ou script do repositório analisado será executado.

## Cinco artefatos

| Artefato | Conteúdo | Papel na análise |
| --- | --- | --- |
| `git-log.json` | SHAs, pais, autoria registrada, datas e mensagens dos commits selecionados. | Histórico, seleção e rastreabilidade; mensagens não comprovam habilidades. |
| `git-diff.json` | Patches de cada commit contra seu pai, caminhos e intervalos de linhas alteradas antes/depois. | Evidência principal do trabalho do usuário; preservar cada commit, sem substituir por um diff acumulado. |
| `languages.json` | Bytes e porcentagens por linguagem com Pygments; linhas de código/comentários/em branco com cloc, além de resumos separados dos arquivos alterados pelo usuário. | Contexto tecnológico; percentuais por bytes e por linhas ficam separados e não estabelecem habilidades. |
| `project-context.json` | Árvore resumida, manifests de dependências e configuração de build, testes e CI, com caminhos e SHAs de origem. | Contexto de arquitetura e ferramentas; presença de uma dependência não estabelece habilidade. |
| `contribution-context.json` | Trechos dos arquivos afetados e métricas Lizard das funções relevantes antes/depois, com origem e intervalos de linhas. | Interpretar os patches; linhas inalteradas e métricas de complexidade/volume são contexto, não evidência atribuível. |

O pacote terá um manifesto de controle, separado dos cinco artefatos, com versão
do schema, repositório, identidade do usuário, SHA de referência, commits
selecionados/excluídos, política de coleta, cobertura, limites, versões dos
coletores e hashes. Catálogo e instruções também ficam separados dos artefatos.
Um artefato sem conteúdo deve registrar essa condição explicitamente.

## Fluxo e componentes

`FastAPI → resolução de identidade/recorte → coleta → pacote → LLM → validação → relatório`

- A API valida o pedido e chama um serviço independente de HTTP, reutilizável pela CLI.
- O núcleo aceita repositórios Git locais ou remotos, independentemente de
  hospedagem. Na API, fontes locais serão identificadores de repositórios
  previamente cadastrados no servidor, não caminhos arbitrários do cliente.
- O usuário é descrito por identidades Git explícitas, inicialmente uma lista de
  e-mails de autoria comparados exatamente. Git não registra um login universal.
  Essa correspondência demonstra autoria registrada, não comprova identidade
  civil, titularidade do e-mail ou originalidade do trabalho.
- Adaptadores opcionais de hospedagem podem resolver contas para identidades e
  commits associados. Cada associação deve registrar sua origem; nomes parecidos
  não bastam. `.mailmap`, se utilizado, é uma declaração versionada do projeto,
  não prova independente de identidade. Não aplicar aliases silenciosamente.
- A referência é resolvida para um SHA antes da seleção. O recorte inicial usa os
  commits alcançáveis nessa referência; commits exclusivos de outras branches ou
  PRs não entram automaticamente. Os pais necessários também são recuperados.
- Os coletores leem objetos Git em um repositório temporário, sem checkout ou
  execução do projeto. Merges e coautoria continuam exigindo revisão explícita.
- A porcentagem de linguagens será calculada por bytes no SHA fixado, com
  classificador e política de exclusão versionados. O MVP usa Pygments local,
  com denominador/exclusões explícitos; Linguist é uma possível evolução e não
  exige integração com a API GitHub.
- O empacotador aplica limites antes da inferência. No MVP, um pacote grande
  demais falha de forma explícita; divisão em lotes será uma evolução.
- O adaptador LLM continua intercambiável. DeepSeek permanece como primeira
  integração, com um novo prompt que distingue evidência e contexto.
- O validador aceita somente IDs do catálogo e citações às linhas alteradas dos
  commits enviados. A checagem usa as identidades selecionadas e a autoria Git
  registrada, permitindo vários e-mails explicitamente associados ao usuário.

## Bibliotecas candidatas

Escolha da fase 1: `pygit2` como backend de objetos; Git CLI preserva a política de
diff existente e pygit2 faz o parsing das linhas. As fixtures comparam os patches
e citações aos do fluxo anterior. Pygments classifica o contexto de linguagens,
sem exigir Ruby/Linguist no MVP. cloc acrescenta contagens por linhas; Lizard
acrescenta métricas das funções afetadas. cloc é um executável externo configurável
e Lizard entra nas dependências Python. As demais bibliotecas continuam candidatas.
Não será necessário implementar todos os backends ou instalar todas as opções.

| Dependência | Uso possível | Consideração |
| --- | --- | --- |
| `pygit2` / libgit2 | Objetos Git, histórico, árvores, blobs e patches com hunks/linhas estruturados. | Bom encaixe para evidências; biblioteca nativa, com instalação e recursos de transporte a conferir no ambiente alvo. |
| `PyDriller` | Coleta pronta de commits, arquivos modificados, linhas adicionadas/removidas e código antes/depois. | Pode acelerar um protótipo; usa GitPython e suas políticas devem ser conferidas antes de reutilizar diffs como citações. Métricas não estabelecem habilidades. |
| `GitPython` | Interface Python de alto nível sobre repositórios e comandos Git. | Facilita a migração do subprocess; a documentação registra limitações de recursos em processos longos, favorecendo processos de coleta com vida limitada. |
| `Dulwich` | Implementação de formatos e protocolos Git em Python, sem exigir o executável Git. | Alternativa quando independência do binário é prioridade; verificar recursos e desempenho necessários. |
| `github-linguist` | Classificação de linguagens, porcentagens por bytes e indicadores de arquivos gerados/vendorizados, em um SHA específico. | Ferramenta local independente da hospedagem; adiciona Ruby e dependências nativas à imagem do serviço. |
| `tree-sitter` e gramáticas selecionadas | Encontrar funções e outros blocos sintáticos ao redor das linhas alteradas. | Evolução do contexto das contribuições; fallback para trechos por linhas em linguagens não cobertas. Não determina habilidades. |

Definir um contrato `GitRepositoryReader` para resolver referências, percorrer
commits, ler árvores/blobs e obter alterações. Coletores, prompt e relatórios não
devem receber objetos específicos de uma biblioteca. A origem do repositório e
os adaptadores de identidade ficam separados do leitor de objetos Git.

## API proposta

`POST /analyses`

```json
{
  "repository": "https://git.example.org/team/project.git",
  "user": {
    "emails": ["dev@example.org", "dev@work.example.org"]
  },
  "ref": "main",
  "max_commits": 50,
  "dry_run": true
}
```

`repository` e `user` são obrigatórios. `user` também poderá ter uma variante
com conta de hospedagem, atendida por adaptador opcional; o modo por e-mails deve
funcionar sem qualquer API de plataforma. Sem `ref`, usar o HEAD anunciado pelo
remoto ou o HEAD do repositório local,
registrando seu SHA resolvido. O recorte padrão proposto são os 50 commits mais
recentes atribuíveis ao usuário e alcançáveis na referência; a ordenação e o
desempate por SHA serão documentados. A cobertura deve indicar que o recorte é
limitado, sem apresentar o resultado como análise de todo o histórico.
Filtros de período podem ser adicionados ao contrato inicial, com semântica
explícita de data de autoria.

Com `dry_run`, devolver manifesto e os cinco artefatos sem chamar a LLM. Na análise
normal, devolver relatório, resumo de cobertura e metadados da execução.
Nenhuma contribuição elegível produz um resultado vazio com motivo explícito.

`GET /health` verifica a disponibilidade do serviço sem acessar remotos ou LLM.

Proposta para o MVP: resposta síncrona com limites de coleta, concorrência e timeout
total, retornando erros estruturados para identidade, acesso, limite e provedor.
Fila durável e endpoints de acompanhamento ficam para a etapa seguinte se o
tempo de execução exigir. Não tratar tarefas em memória como fila persistente.

Implementação da fase 1: resposta síncrona, um processo temporário por análise,
120 segundos de timeout total e concorrência padrão de duas análises por processo
da API. Fontes remotas precisam ter hosts habilitados no servidor. As identidades
por e-mail estão implementadas; adaptadores de contas de hospedagem são extensão futura.

## Contratos e compatibilidade

- Criar contratos versionados para pedido, pacote e relatório por usuário.
- O relatório novo registra seletor de identidade e sua origem, conta de
  hospedagem quando disponível, referência resolvida, recorte, autoria por commit,
  catálogo e citações. Cada habilidade continua exigindo
  evidência própria; contexto não autoriza atribuição.
- Preservar `supported`, `insufficient_evidence` e `catalog_gap`, sem níveis de
  proficiência ou criação automática de entradas no catálogo.
- Manter validação dos relatórios v1 e comandos existentes; o fluxo novo usa v2
  quando necessário para representar identidade e cobertura sem ambiguidade.
- Reaproveitar os helpers de diff, intervalos de linhas e validação do catálogo.
  Generalizar a preparação da inferência e a verificação restrita a um commit.
- Manter conteúdo coletado como dado não confiável e registrar hashes do pacote,
  catálogo, prompt e resposta/metadados. Não confundir hashes com determinismo da LLM.

## Entregas — fase 1: coleta Git e API

1. Revisar esta proposta e atualizar `docs/spec.md`: artefatos, recorte,
   atribuição, contratos, limites e compatibilidade.
2. Definir o leitor Git, avaliar o backend e implementar resolução de
   repositório/usuário e coleta de múltiplos commits, com fixtures locais e
   um remoto Git de teste independente de APIs de hospedagem.
3. Implementar os cinco coletores, manifesto e preview reproduzível.
4. Adaptar prompt, provedor e validação de citações para o pacote.
5. Adicionar FastAPI, configuração, documentação de execução e testes HTTP.

Critérios de aceite: receber repositório e identidades Git sem depender de GitHub;
produzir exatamente cinco
artefatos; expor cobertura e exclusões; suportar vários commits/e-mails com
atribuição rastreável; rejeitar IDs inventados e citações externas ao recorte;
preservar a CLI; concluir um fluxo HTTP com LLM simulada e validar casos de
CSS/documentação em projetos de outras tecnologias. Uma avaliação humana
separada verificará a qualidade semântica das inferências.

## Fase 2: referência em grafo e competências estruturadas para a LLM

Adicionar uma referência mais estruturada do roadmap.sh, representando competências,
subcompetências, tecnologias e suas relações em um grafo versionado. O grafo será
uma evolução do catálogo explícito, com suporte a outras fontes, sem acoplamento
à hospedagem do repositório analisado.

### Referência e relações

- Cada competência e subcompetência mantém ID estável, nome, descrição, aliases,
  critérios de evidência e referências de origem com revisões imutáveis.
- Tecnologias terão identificadores e aliases controlados, com associação explícita
  aos tópicos pertinentes. Uma tecnologia pode se relacionar a várias competências;
  sua presença não demonstra todas elas.
- As arestas têm tipos explícitos, como `subcompetencia_de`, `relacionada_a` e
  `tecnologia_associada_a`, com origem e revisão. Não derivar uma hierarquia apenas
  da posição visual de um tópico no roadmap.
- Importação, consolidação de tópicos equivalentes e revisão dos critérios ocorrem
  separadamente da extração. Publicar snapshots imutáveis do catálogo/grafo e
  registrar versão e hash em cada execução.
- Usar o grafo para organizar a referência e recuperar candidatos/contexto para
  a LLM. Percorrer uma aresta não autoriza atribuir uma competência, subcompetência,
  pré-requisito ou papel profissional: cada atribuição exige evidência própria.

### Saída estruturada da preparação, enviada à LLM

A saída desta etapa será uma lista de registros no formato solicitado, preparada
a partir dos cinco artefatos e vinculada aos candidatos do catálogo/grafo:

```json
{
  "competência": "Network security",
  "nível": "presente",
  "tecnologias": ["TLS", "SSL"],
  "evidências": ["ev-001", "ev-002", "ev-003"]
}
```

O exemplo é ilustrativo: esses rótulos só poderão ser usados depois de sua inclusão
e revisão na referência versionada. Não representam entradas já existentes no
catálogo inicial nem uma avaliação real.

- `competência` é o nome de uma entrada existente; seu ID estável acompanha o
  registro no envelope de controle, evitando ambiguidade entre nomes e versões.
- `nível: presente` expressa presença proposta com evidências no recorte, não
  proficiência. Como o registro é entrada da LLM, a proposta ainda pode ser
  rejeitada na avaliação; não tratá-la como conclusão confirmada antes dessa etapa.
- `tecnologias` contém nomes controlados ligados às evidências do trabalho do
  usuário. Linguagens, dependências ou tecnologias do projeto sem contribuição
  pertinente não bastam para preencher esse campo.
- `evidências` referencia um registro de evidências no mesmo pacote, contendo
  repositório, SHA completo, caminho, lado do diff, linhas e trecho correspondente.
  Descrições textuais podem acompanhar as referências, mas não substituí-las.
- O envelope registra schema, catálogo/grafo, IDs dos candidatos, identidade,
  recorte, hashes dos artefatos e estado de proposta. Ele e a referência em grafo
  são dados de controle, mantendo exatamente cinco artefatos de coleta.
- Ausência de um registro `presente` não significa ausência de habilidade. O
  resultado validado mantém `insufficient_evidence` e `catalog_gap` distintos,
  além de `supported`, com justificativas e sem novas entradas automáticas.

Fluxo da fase 2:

`cinco artefatos → recuperação de candidatos no grafo → registros de competência
com evidências → avaliação pela LLM → validação de IDs, decisões e citações`

### Entregas e aceite da fase 2

1. Definir schema de nós, tecnologias e arestas; importar e revisar um subconjunto
   do roadmap.sh com referências fixadas.
2. Implementar busca de candidatos no grafo e o contrato dos registros enviados
   à LLM, incluindo o registro de evidências e os IDs no envelope.
3. Adaptar prompt e validação para avaliar as propostas sem herdar competências
   pelas relações do grafo.
4. Avaliar recuperação e decisão separadamente com exemplos revisados: tecnologia
   apenas nas dependências, evidência de uma subcompetência sem evidência do pai,
   referências inválidas e lacunas do catálogo.

Aceite: preview da entrada estruturada; reprodução do mesmo snapshot de referência;
rejeição de rótulos/IDs desconhecidos e evidências inválidas; rastreabilidade de
tecnologias e competências; nenhuma atribuição automática pela hierarquia;
preservação das distinções entre presença sustentada, evidência insuficiente e
lacuna de cobertura. Níveis de proficiência permanecem fora desta fase.

## Referências consultadas

- GitHub, commits e filtro por autor:
  https://docs.github.com/en/rest/commits/commits#list-commits
- GitHub, linguagens e contagem por bytes:
  https://docs.github.com/en/rest/repos/repos#list-repository-languages
- FastAPI, limites de tarefas em segundo plano e alternativas:
  https://fastapi.tiangolo.com/tutorial/background-tasks/
- pygit2, patches estruturados e instalação:
  https://www.pygit2.org/diff.html
  https://www.pygit2.org/install.html
- PyDriller, dados de arquivos modificados:
  https://pydriller.readthedocs.io/en/latest/modifiedfile.html
- GitPython, requisitos e limitações:
  https://gitpython.readthedocs.io/en/stable/intro.html
- Dulwich, implementação e compatibilidade:
  https://dulwich.readthedocs.io/en/stable/
- Linguist, execução local e referência de commit:
  https://github.com/github-linguist/linguist
- Tree-sitter, parsing e gramáticas:
  https://tree-sitter.github.io/tree-sitter/
