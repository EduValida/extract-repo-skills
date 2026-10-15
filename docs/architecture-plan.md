# Arquitetura: preparação local por repositório e usuário

Status: coleta estática e preview implementados; contrato independente de preparação,
separação da inferência e fase 2 semântica planejados. Contratos e execução:
`docs/spec.md` e `README.md`.

## Objetivo

Preparar exclusivamente o I/O local das etapas estática e de preparação semântica:
receber um repositório Git, identidades de autoria, recorte e catálogo versionado;
coletar contribuições; organizar contexto e candidatos com evidências; validar e
exportar um pacote para outro projeto consumir.

O envio à LLM, o processamento de sua resposta e a produção de avaliações finais
pertencem ao projeto consumidor. A integração existente neste repositório é legado
a separar, não uma entrega deste plano. A saída local contém propostas rastreáveis,
não habilidades confirmadas. A preparação semântica deve usar somente entradas
existentes na referência versionada.
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

`entrada local/API → identidade e recorte → cinco artefatos + manifesto → preparação semântica local → validação do pacote → exportação`

O projeto consumidor recebe o pacote exportado e assume a inferência. Atualmente,
`collect_bundle()` já entrega artefatos e manifesto. `dry_run: true` expõe esse
pacote e uma requisição preparada sem chamar a LLM. Essa requisição ainda contém
mensagens e schema de resposta; o contrato independente de exportação e os
registros semânticos abaixo ainda não foram implementados.

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
- O empacotador aplica limites antes da exportação. No MVP, um pacote grande
  demais falha de forma explícita; divisão em lotes será uma evolução.
- A preparação semântica recupera candidatos da referência e organiza evidências
  por candidato. Métricas, tecnologias e relações do grafo não confirmam habilidades.
- O validador local aceita somente IDs da referência e evidências atribuíveis às
  linhas alteradas dos commits selecionados. A checagem usa as identidades
  selecionadas e a autoria Git registrada, permitindo vários e-mails explicitamente
  associados ao usuário. Não recebe respostas de modelos para avaliação.
- O exportador expõe um contrato versionado independente de provedor, formato de
  mensagens, credenciais ou schema de avaliação final.

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
commits, ler árvores/blobs e obter alterações. Coletores e pacotes exportados não
devem receber objetos específicos de uma biblioteca. A origem do repositório e
os adaptadores de identidade ficam separados do leitor de objetos Git.

## API existente e adaptação necessária

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

Hoje `dry_run: true` devolve manifesto, cinco artefatos e a requisição preparada,
sem chamar a LLM. O padrão `false` ainda aciona o fluxo legado de inferência e
relatório. A interface deve ser adaptada para tornar preparação/exportação sua
operação principal e devolver o pacote independente descrito neste plano.
O contrato dessa adaptação ainda precisa ser definido e versionado; o exemplo
acima documenta a API atual, não um endpoint novo já implementado.
Nenhuma contribuição elegível deve gerar um pacote com seleção vazia e motivo
explícito, sem concluir que a pessoa não possui habilidades.

`GET /health` verifica a disponibilidade do serviço sem acessar remotos ou LLM.

Proposta para o MVP: resposta síncrona com limites de coleta, concorrência e timeout
total, retornando erros estruturados para identidade, acesso, coleta e limites.
Fila durável e endpoints de acompanhamento ficam para a etapa seguinte se o
tempo de execução exigir. Não tratar tarefas em memória como fila persistente.

Implementação da fase 1: resposta síncrona, um processo temporário por análise,
120 segundos de timeout total e concorrência padrão de duas análises por processo
da API. Fontes remotas precisam ter hosts habilitados no servidor. As identidades
por e-mail estão implementadas; adaptadores de contas de hospedagem são extensão futura.

## Contratos e compatibilidade

- Criar contratos versionados para pedido, pacote estático e preparação semântica.
- O envelope exportado registra seletor de identidade e sua origem, conta de
  hospedagem quando disponível, referência resolvida, recorte, autoria por commit,
  catálogo e evidências. Cada candidato precisa de vínculos explícitos;
  contexto não autoriza atribuição de habilidade.
- Distinguir evidência insuficiente de lacuna da referência, sem níveis de
  proficiência ou criação automática de entradas. As decisões finais
  `supported`, `insufficient_evidence` e `catalog_gap` são do projeto consumidor.
- Planejar a migração do fluxo legado de inferência e relatórios para o consumidor;
  não apresentar os contratos de relatório v1/v2 como a saída local deste projeto.
- Reaproveitar os helpers de diff, intervalos de linhas e validação do catálogo.
  Generalizar a preparação local e a verificação de evidências restrita a um commit.
- Manter conteúdo coletado como dado não confiável e registrar hashes do pacote,
  catálogo e configuração de preparação. Hashes identificam dados e configuração;
  não comprovam correção semântica dos candidatos.

## Entregas — fase 1: coleta Git e API

1. Revisar esta proposta e atualizar `docs/spec.md`: artefatos, recorte,
   atribuição, contratos, limites e compatibilidade.
2. Definir o leitor Git, avaliar o backend e implementar resolução de
   repositório/usuário e coleta de múltiplos commits, com fixtures locais e
   um remoto Git de teste independente de APIs de hospedagem.
3. Implementar os cinco coletores, manifesto e preview reproduzível.
4. Definir contrato independente de I/O, validação local e exportação do pacote;
   separar a chamada ao provedor e o processamento da resposta.
5. Adicionar FastAPI, configuração, documentação de execução e testes HTTP.

Critérios de aceite: receber repositório e identidades Git sem depender de GitHub;
produzir exatamente cinco
artefatos; expor cobertura e exclusões; suportar vários commits/e-mails com
atribuição rastreável; validar referências e linhas de evidência; concluir coleta
e exportação sem dependências ou credenciais de modelo. Casos de CSS/documentação
em projetos de outras tecnologias devem preservar a separação entre trabalho
atribuível e contexto. A aceitação desta fase não depende de chamadas à LLM,
respostas simuladas ou relatórios finais.

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

### Saída estruturada da preparação, exportada ao projeto consumidor

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
  proficiência. A avaliação no projeto consumidor ainda pode rejeitar a proposta;
  este projeto não a transforma em conclusão confirmada.
- `tecnologias` contém nomes controlados ligados às evidências do trabalho do
  usuário. Linguagens, dependências ou tecnologias do projeto sem contribuição
  pertinente não bastam para preencher esse campo.
- `evidências` referencia um registro de evidências no mesmo pacote, contendo
  repositório, SHA completo, caminho, lado do diff, linhas e trecho correspondente.
  Descrições textuais podem acompanhar as referências, mas não substituí-las.
- O envelope registra schema, catálogo/grafo, IDs dos candidatos, identidade,
  recorte, hashes dos artefatos e estado de proposta. Ele e a referência em grafo
  são dados de controle, mantendo exatamente cinco artefatos de coleta.
- Ausência de um registro `presente` não significa ausência de habilidade.
  A preparação explicita evidência insuficiente e lacunas da referência sem
  criar novas entradas. Decisões de avaliação final pertencem ao consumidor.

Fluxo da fase 2:

`cinco artefatos → recuperação de candidatos no grafo → registros candidatos
com evidências → validação local de IDs e referências → exportação`

### Entregas e aceite da fase 2

1. Definir schema de nós, tecnologias e arestas; importar e revisar um subconjunto
   do roadmap.sh com referências fixadas.
2. Implementar busca de candidatos no grafo e o contrato dos registros exportados,
   incluindo o registro de evidências e os IDs no envelope.
3. Validar o pacote e suas propostas sem atribuir habilidades pelas relações do
   grafo ou depender do formato de resposta de uma LLM.
4. Avaliar recuperação de candidatos e vínculos de evidência com exemplos revisados: tecnologia
   apenas nas dependências, evidência de uma subcompetência sem evidência do pai,
   referências inválidas e lacunas do catálogo.

Aceite: preview da entrada estruturada; reprodução do mesmo snapshot de referência;
rejeição de rótulos/IDs desconhecidos e evidências inválidas; rastreabilidade de
tecnologias e competências; nenhuma atribuição automática pela hierarquia;
distinção entre candidatos, evidência insuficiente e lacuna de cobertura;
exportação completa sem inferência. Níveis de proficiência permanecem fora desta fase.

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
