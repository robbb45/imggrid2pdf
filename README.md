# imggrid2pdf

Editor de imagens para folhas A4 e PDF a 300 dpi. Execute `launch_ui.ps1` ou `launch_ui.bat` para abrir o programa.

## Composição da página

Na seção **Composição da página**, escolha uma das abas:

- **Grid**: mantém as molduras quadradas e a grade regular de 4, 6, 9 ou 12 imagens.
- **Encaixe automático**: cria molduras retangulares a partir da proporção das imagens já recortadas. Escolha de 1 a 100 imagens por página completa; a última recebe as restantes. Os arquivos continuam nos mesmos grupos de páginas, mas as posições dentro de cada folha podem mudar. A numeração acompanha o arquivo.

O Encaixe automático usa tamanhos livres por padrão, procurando ampliar cada figura no espaço disponível. A opção **Padronizar por mesma área** faz todas as figuras terem a mesma área impressa em todo o PDF, incluindo a última página. Uma figura de 10 × 4 cm e uma de 5 × 8 cm, por exemplo, têm os mesmos 40 cm². Pequenas diferenças de arredondamento para pixels são possíveis.

**Área máxima (cm²)**, **Largura máx. (cm)** e **Altura máx. (cm)** são limites opcionais. Zero significa automático. Os limites se referem à figura, sem a moldura e a margem interna. Com a mesma área ativada, uma restrição em uma figura pode reduzir o tamanho do conjunto para manter a padronização.

O giro de 90° é opcional e fica desativado por padrão. As bordas, seus estilos e cantos, as cores, a numeração com brilho, a remoção de fundo, as margens internas e os deslocamentos continuam disponíveis por imagem. O encaixe apenas redimensiona e posiciona: o tratamento de fundo e o recorte de espaços em branco usam os controles existentes.

A prévia informa a área resultante das figuras em cm². O algoritmo testa diferentes encaixes MaxRects e escalas; procura um bom aproveitamento, sem garantir o ótimo matemático ou o preenchimento integral da folha.

Para ocultar o número ou nome no canto, desmarque **Número/nome → Mostrar** nos ajustes da imagem. Isso oculta também o brilho atrás do texto, tanto nas prévias quanto no PDF, nos dois modos de composição. Dê duplo clique no nome do ajuste para aplicar às outras imagens, ou altere o padrão em **Configurações Globais → Mostrar número/nome nas imagens**.

## Verificação

Com Pillow instalado:

```powershell
python -m unittest discover -s tests -v
```

Os testes cobrem encaixe, proporções, limites físicos, espaçamentos, padronização opcional, bordas, numeração, cache, seleção na prévia, geração de PDF e integração das abas. O teste das abas requer Tk disponível.
